from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import jwt
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from equipments_clone import __version__

VALID_STATUSES = {"AVAILABLE", "RESERVED", "DISPATCHED", "IN_TRANSIT", "RETURNED", "RELEASED"}
PUBLIC_PATHS = {"/", "/health", "/openapi.json", "/dev/generate-token"}
PUBLIC_PREFIXES = ("/playground",)


class EquipmentTypeCreate(BaseModel):
    code: str
    description: str
    nominalLength: str
    maxPayloadKg: int


class EquipmentTypeUpdate(BaseModel):
    description: str | None = None
    nominalLength: str | None = None
    maxPayloadKg: int | None = None


class ContainerCreate(BaseModel):
    containerNumber: str
    equipmentType: str
    currentDepot: str


class ContainerStatusUpdate(BaseModel):
    status: str


class ReservationLine(BaseModel):
    type: str
    quantity: int = Field(gt=0)


class ReservationCreate(BaseModel):
    bookingReference: str
    originDepot: str
    equipment: list[ReservationLine]


class EventIn(BaseModel):
    eventType: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DevTokenRequest(BaseModel):
    subject: str
    scopes: list[str] = Field(default_factory=list)
    role: str | None = None


@dataclass(frozen=True)
class RuntimeConfig:
    backend: str
    path: str | None
    environment: str
    empty_on_first_boot: bool


@dataclass(frozen=True)
class AuthContext:
    issuer: str
    subject: str
    scopes: frozenset[str]
    role: str | None

    def is_admin(self) -> bool:
        return self.role == "admin"


security = HTTPBearer(auto_error=False, scheme_name="bearerAuth")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def public_route(path: str) -> bool:
    return path in PUBLIC_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def runtime_config_from_env(env: dict[str, str] | None = None) -> RuntimeConfig:
    values = env if env is not None else os.environ
    backend = values.get("STORAGE_BACKEND", "memory").lower()
    environment = values.get("APP_ENV", values.get("NODE_ENV", "development")).lower()
    empty_on_first_boot = values.get("STORAGE_SQLITE_EMPTY_ON_FIRST_BOOT", "").lower() == "true"

    if backend == "memory":
        return RuntimeConfig("memory", None, environment, empty_on_first_boot)
    if backend == "db":
        path = values.get("STORAGE_DB_PATH")
        if not path:
            raise RuntimeError("STORAGE_DB_PATH is required")
        return RuntimeConfig("sqlite", path, environment, empty_on_first_boot)
    if backend == "sqlite":
        path = values.get("STORAGE_SQLITE_PATH") or values.get("STORAGE_DB_PATH")
        if not path:
            raise RuntimeError("STORAGE_SQLITE_PATH or STORAGE_DB_PATH is required")
        return RuntimeConfig("sqlite", path, environment, empty_on_first_boot)
    if backend == "postgres":
        if not values.get("STORAGE_POSTGRES_URL"):
            raise RuntimeError("STORAGE_POSTGRES_URL is required")
        raise RuntimeError("postgres storage is not implemented in this Python clone")
    raise RuntimeError(f"unsupported storage backend {backend}")


def auth_settings() -> tuple[str, str, str]:
    return (
        os.environ.get("AUTH_JWT_ISSUER", "platform-auth"),
        os.environ.get("AUTH_JWT_AUDIENCE", "equipments-service"),
        os.environ.get("AUTH_JWT_SECRET", "equipments-dev-secret"),
    )


def make_token(
    subject: str,
    scopes: list[str] | None = None,
    role: str | None = None,
    expires_in: int = 3600,
) -> str:
    issuer, audience, secret = auth_settings()
    payload: dict[str, Any] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "exp": int((datetime.now(UTC) + timedelta(seconds=expires_in)).timestamp()),
    }
    if scopes:
        payload["scope"] = " ".join(scopes)
    if role:
        payload["role"] = role
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str) -> AuthContext:
    issuer, audience, secret = auth_settings()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["sub", "iss", "aud", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token is expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "bearer token audience is invalid"
        ) from exc
    except jwt.InvalidIssuerError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token issuer is invalid") from exc
    except jwt.InvalidSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bearer token signature") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bearer token") from exc

    scope_claim = payload.get("scope", "")
    if isinstance(scope_claim, str):
        scopes = frozenset(scope for scope in scope_claim.split() if scope)
    elif isinstance(scope_claim, list):
        scopes = frozenset(str(scope) for scope in scope_claim)
    else:
        scopes = frozenset()
    return AuthContext(
        issuer=str(payload["iss"]),
        subject=str(payload["sub"]),
        scopes=scopes,
        role=payload.get("role"),
    )


def require_auth(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return decode_token(credentials.credentials)


def require_scope(scope: str) -> Callable[[AuthContext], AuthContext]:
    def dependency(context: Annotated[AuthContext, Depends(require_auth)]) -> AuthContext:
        if context.is_admin() or scope in context.scopes:
            return context
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing required scope {scope}")

    return dependency


RequireRead = Annotated[AuthContext, Depends(require_scope("equipments:read"))]
RequireModify = Annotated[AuthContext, Depends(require_scope("equipments:modify"))]


class EquipmentStore:
    def __init__(self, config: RuntimeConfig, seed: bool = True) -> None:
        self.config = config
        self.equipment_types: dict[str, dict[str, Any]] = {}
        self.containers: dict[str, dict[str, Any]] = {}
        self.reservations: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.audit_events: list[dict[str, Any]] = []
        loaded = self._load()
        if not loaded and seed and not config.empty_on_first_boot:
            self.seed()

    def _load(self) -> bool:
        if self.config.backend != "sqlite" or self.config.path is None:
            return False
        path = Path(self.config.path)
        if not path.exists():
            return False
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, data TEXT NOT NULL)"
            )
            row = conn.execute("SELECT data FROM state WHERE id = 1").fetchone()
        if row is None:
            return False
        data = json.loads(row[0])
        self.equipment_types = data.get("equipment_types", {})
        self.containers = data.get("containers", {})
        self.reservations = data.get("reservations", {})
        self.users = data.get("users", {})
        self.audit_events = data.get("audit_events", [])
        return True

    def _save(self) -> None:
        if self.config.backend != "sqlite" or self.config.path is None:
            return
        path = Path(self.config.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(
            {
                "equipment_types": self.equipment_types,
                "containers": self.containers,
                "reservations": self.reservations,
                "users": self.users,
                "audit_events": self.audit_events,
            },
            sort_keys=True,
        )
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, data TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO state (id, data) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (data,),
            )
            conn.commit()

    def seed(self) -> None:
        self.clear(seed=False)
        for code, description, nominal_length, max_payload in [
            ("20FT", "Standard 20-foot dry container", "20'", 28200),
            ("40FT", "Standard 40-foot dry container", "40'", 26500),
            ("40HC", "40-foot High Cube", "40'", 26460),
            ("20RF", "20-foot Reefer", "20'", 27400),
            ("40RF", "40-foot Reefer High Cube", "40'", 26380),
        ]:
            self.equipment_types[code] = self._equipment_payload(
                code, description, nominal_length, max_payload
            )
        for number, equipment_type in [
            ("CONU1234567", "20FT"),
            ("CONU7654321", "20FT"),
            ("CONU1111111", "20FT"),
            ("CONU3000001", "40FT"),
            ("CONU3000002", "40FT"),
            ("CONU4000001", "40HC"),
        ]:
            self._create_container(number, equipment_type, "CNSHA-01", None)
        self._save()

    def clear(self, seed: bool) -> None:
        self.equipment_types.clear()
        self.containers.clear()
        self.reservations.clear()
        self.users.clear()
        self.audit_events.clear()
        if seed:
            self.seed()
        else:
            self._save()

    def local_user(self, issuer: str, subject: str) -> dict[str, Any]:
        key = f"{issuer}:{subject}"
        existing = self.users.get(key)
        if existing is not None:
            return existing
        user = {
            "id": f"user-{uuid4()}",
            "issuer": issuer,
            "subject": subject,
            "createdAt": utc_now(),
        }
        self.users[key] = user
        self._save()
        return user

    def actor_for(self, request: Request, context: AuthContext) -> dict[str, Any]:
        issuer = request.headers.get("x-auth-issuer")
        subject = request.headers.get("x-auth-subject")
        if bool(issuer) != bool(subject):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "authenticated caller metadata requires both x-auth-issuer "
                "and x-auth-subject headers",
            )
        if issuer and subject:
            return self.local_user(issuer, subject)
        return self.local_user(context.issuer, context.subject)

    def _metadata(
        self,
        created_by: dict[str, Any] | None,
        modified_by: dict[str, Any] | None,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = utc_now()
        created_at = existing.get("createdAt") if existing else timestamp
        created_user = existing.get("createdByUser") if existing else created_by
        created_user_id = existing.get("createdByUserId") if existing else (
            created_by["id"] if created_by else None
        )
        return {
            "createdAt": created_at,
            "updatedAt": timestamp,
            "createdByUserId": created_user_id,
            "lastModifiedByUserId": modified_by["id"] if modified_by else None,
            "createdByUser": created_user,
            "lastModifiedByUser": modified_by,
        }

    def _equipment_payload(
        self,
        code: str,
        description: str,
        nominal_length: str,
        max_payload: int,
        actor: dict[str, Any] | None = None,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = self._metadata(actor, actor, existing)
        return {
            "code": code,
            "description": description,
            "nominalLength": nominal_length,
            "maxPayloadKg": max_payload,
            **metadata,
        }

    def list_equipment_types(self) -> list[dict[str, Any]]:
        return list(self.equipment_types.values())

    def create_equipment_type(
        self, body: EquipmentTypeCreate, actor: dict[str, Any]
    ) -> dict[str, Any]:
        code = body.code.upper()
        if code in self.equipment_types:
            raise HTTPException(status.HTTP_409_CONFLICT, f"equipment type {code} already exists")
        payload = self._equipment_payload(
            code, body.description, body.nominalLength, body.maxPayloadKg, actor
        )
        self.equipment_types[code] = payload
        self._audit(actor, "equipment_type.create", "equipment_type", code, "success")
        self._save()
        return payload

    def update_equipment_type(
        self, code: str, body: EquipmentTypeUpdate, actor: dict[str, Any]
    ) -> dict[str, Any]:
        normalized = code.upper()
        existing = self.equipment_types.get(normalized)
        if existing is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"equipment type {normalized} not found"
            )
        updated = self._equipment_payload(
            normalized,
            body.description if body.description is not None else existing["description"],
            body.nominalLength if body.nominalLength is not None else existing["nominalLength"],
            body.maxPayloadKg if body.maxPayloadKg is not None else existing["maxPayloadKg"],
            actor,
            existing,
        )
        self.equipment_types[normalized] = updated
        self._audit(actor, "equipment_type.update", "equipment_type", normalized, "success")
        self._save()
        return updated

    def _create_container(
        self,
        container_number: str,
        equipment_type: str,
        current_depot: str,
        actor: dict[str, Any] | None,
    ) -> dict[str, Any]:
        container_id = str(uuid4())
        metadata = self._metadata(actor, actor)
        container = {
            "id": container_id,
            "containerNumber": container_number,
            "equipmentType": equipment_type,
            "status": "AVAILABLE",
            "currentDepot": current_depot,
            "bookingReference": None,
            "lastMovedAt": utc_now(),
            **metadata,
        }
        self.containers[container_id] = container
        return container

    def register_container(self, body: ContainerCreate, actor: dict[str, Any]) -> dict[str, Any]:
        equipment_type = body.equipmentType.upper()
        if equipment_type not in self.equipment_types:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"unknown equipment type {equipment_type}"
            )
        container = self._create_container(
            body.containerNumber, equipment_type, body.currentDepot, actor
        )
        self._audit(actor, "container.create", "container", container["id"], "success")
        self._save()
        return container

    def list_containers(
        self,
        equipment_type: str | None,
        state: str | None,
        depot: str | None,
    ) -> list[dict[str, Any]]:
        containers = list(self.containers.values())
        if equipment_type:
            containers = [c for c in containers if c["equipmentType"] == equipment_type.upper()]
        if state:
            containers = [c for c in containers if c["status"] == state.upper()]
        if depot:
            containers = [c for c in containers if c["currentDepot"] == depot]
        return containers

    def get_container(self, container_id: str) -> dict[str, Any]:
        container = self.containers.get(container_id)
        if container is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"container {container_id} not found")
        return container

    def set_container_status(
        self, container_id: str, new_status: str, actor: dict[str, Any]
    ) -> dict[str, Any]:
        container = self.get_container(container_id)
        status_value = new_status.upper()
        if status_value not in VALID_STATUSES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"invalid container status {status_value}"
            )
        self._touch_container(container, actor)
        container["status"] = status_value
        container["lastMovedAt"] = utc_now()
        if status_value == "AVAILABLE":
            container["bookingReference"] = None
        self._audit(actor, "container.status", "container", container_id, "success")
        self._save()
        return container

    def availability(self, depot_code: str | None) -> list[dict[str, Any]]:
        counts = Counter(
            container["equipmentType"]
            for container in self.containers.values()
            if container["status"] == "AVAILABLE"
            and (depot_code is None or container["currentDepot"] == depot_code)
        )
        return [
            {"equipmentType": equipment_type, "availableCount": count, "depotCode": depot_code}
            for equipment_type, count in sorted(counts.items())
            if count > 0
        ]

    def create_reservation(
        self, body: ReservationCreate, actor: dict[str, Any]
    ) -> dict[str, Any]:
        if body.bookingReference in self.reservations:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"booking {body.bookingReference} already has a reservation",
            )
        assignments: list[dict[str, Any]] = []
        for line in body.equipment:
            equipment_type = line.type.upper()
            available = [
                c
                for c in self.containers.values()
                if c["equipmentType"] == equipment_type
                and c["status"] == "AVAILABLE"
                and c["currentDepot"] == body.originDepot
            ]
            if len(available) < line.quantity:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"insufficient available {equipment_type} at depot {body.originDepot}",
                )
            assignments.extend(available[: line.quantity])

        reservation_id = f"RES-{uuid4()}"
        for container in assignments:
            self._touch_container(container, actor)
            container["status"] = "RESERVED"
            container["bookingReference"] = body.bookingReference
            container["lastMovedAt"] = utc_now()

        reservation = {
            "id": reservation_id,
            "reservationId": reservation_id,
            "bookingReference": body.bookingReference,
            "assignedContainers": [
                {"containerId": c["id"], "type": c["equipmentType"]} for c in assignments
            ],
            "containers": [c["id"] for c in assignments],
            "status": "ACTIVE",
            **self._metadata(actor, actor),
        }
        self.reservations[body.bookingReference] = reservation
        self._audit(actor, "reservation.create", "reservation", reservation_id, "success")
        self._save()
        return reservation

    def release_reservation(self, booking_reference: str, actor: dict[str, Any]) -> dict[str, Any]:
        reservation = self.reservations.get(booking_reference)
        if reservation is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"reservation for booking {booking_reference} not found",
            )
        assigned = [self.containers[cid] for cid in reservation["containers"]]
        if any(c["status"] in {"DISPATCHED", "IN_TRANSIT"} for c in assigned):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "reservation cannot be released after dispatch",
            )
        for container in assigned:
            self._touch_container(container, actor)
            container["status"] = "AVAILABLE"
            container["bookingReference"] = None
            container["lastMovedAt"] = utc_now()
        reservation.update(self._metadata(None, actor, reservation))
        reservation["status"] = "RELEASED"
        self._audit(actor, "reservation.release", "reservation", reservation["id"], "success")
        self._save()
        return reservation

    def pickup(self, container_id: str, actor: dict[str, Any]) -> dict[str, Any]:
        container = self.get_container(container_id)
        if container["status"] != "RESERVED":
            raise HTTPException(
                status.HTTP_409_CONFLICT, "pickup allowed only when status is RESERVED"
            )
        self._touch_container(container, actor)
        container["status"] = "DISPATCHED"
        container["lastMovedAt"] = utc_now()
        self._audit(actor, "container.pickup", "container", container_id, "success")
        self._save()
        return container

    def return_container(self, container_id: str, actor: dict[str, Any]) -> dict[str, Any]:
        container = self.get_container(container_id)
        if container["status"] not in {"DISPATCHED", "IN_TRANSIT"}:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "return allowed only when status is DISPATCHED or IN_TRANSIT",
            )
        self._touch_container(container, actor)
        container["status"] = "AVAILABLE"
        container["bookingReference"] = None
        container["lastMovedAt"] = utc_now()
        self._audit(actor, "container.return", "container", container_id, "success")
        self._save()
        return container

    def process_event(self, body: EventIn, actor: dict[str, Any]) -> dict[str, Any]:
        booking_reference = str(body.payload.get("bookingReference", ""))
        if body.eventType == "booking.cancelled":
            self.release_reservation(booking_reference, actor)
            return {"processed": True}
        if body.eventType == "booking.completed":
            reservation = self.reservations.get(booking_reference)
            if reservation is None:
                return {"processed": False}
            for container_id in reservation["containers"]:
                container = self.containers[container_id]
                if container["status"] in {"DISPATCHED", "IN_TRANSIT"}:
                    self.return_container(container_id, actor)
            return {"processed": True}
        self._audit(actor, body.eventType, "event", booking_reference, "success")
        self._save()
        return {"processed": True}

    def _touch_container(self, container: dict[str, Any], actor: dict[str, Any]) -> None:
        container.update(self._metadata(None, actor, container))

    def _audit(
        self,
        actor: dict[str, Any],
        action: str,
        resource_type: str,
        resource_id: str,
        outcome: str,
    ) -> None:
        self.audit_events.append(
            {
                "id": str(uuid4()),
                "actorUserId": actor["id"],
                "action": action,
                "resourceType": resource_type,
                "resourceId": resource_id,
                "outcome": outcome,
                "timestamp": utc_now(),
            }
        )


def error_response(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


def validation_error_response(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        {"error": "request validation failed", "details": exc.errors()}, status_code=422
    )


def create_app() -> FastAPI:
    config = runtime_config_from_env()
    store = EquipmentStore(config)
    app = FastAPI(title="Equipments Service API", version=__version__, openapi_version="3.1.0")
    app.state.store = store

    app.add_exception_handler(HTTPException, error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/playground", status_code=status.HTTP_302_FOUND)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/playground", response_class=HTMLResponse, include_in_schema=False)
    def playground() -> str:
        dev = store.config.environment != "production"
        dev_controls = (
            "<button>Reset All Data</button><button>Clear All Data</button>"
            if dev
            else "<p>Dev-only actions unavailable outside development mode</p>"
        )
        return f"""<!doctype html>
<html>
<head>
  <title>Equipments API Playground</title>
  <link rel="stylesheet" href="/playground/playground.css">
</head>
<body>
  <h1>Equipments API Playground</h1>
  <section class="auth-panel">
    <strong>Active Backend</strong>
    <span class="backend-chip">{store.config.backend}</span>
    <span>{store.config.path or "no persistent path"}</span>
    <label>Bearer token <input id="bearer-token"></label>
    <label>Token subject <input id="token-subject"></label>
    <label>Token rights</label>
    <button>Generate Token</button>
    <p>equipments:read equipments:modify role=admin</p>
    <p>Protected routes without equipment scopes</p>
    <p>GET /health /openapi.json Dev-only actions</p>
    {dev_controls}
  </section>
  <script src="/playground/playground.js"></script>
</body>
</html>"""

    @app.get("/playground/playground.css", include_in_schema=False)
    def playground_css() -> Response:
        return Response(
            ".backend-chip{font-weight:700}.auth-panel{border:1px solid #ccc;padding:1rem}",
            media_type="text/css",
        )

    @app.get("/playground/playground.js", include_in_schema=False)
    def playground_js() -> Response:
        script = """
const presets = { availability: {}, updateType: {}, getContainer: {}, authHint: {} };
const bearerTokenInput = document.querySelector("#bearer-token");
const generateTokenButton = document.querySelector("button");
function generateToken(subject, rights) { return fetch("/dev/generate-token"); }
function roleFromSelection(selection) { return selection === "admin" ? "admin" : undefined; }
function isPublicPath(path) { return ["/health", "/openapi.json"].includes(path); }
function resetResponseOutput() {}
function runDevDataAction(action) { resetResponseOutput(); return action; }
function resetAllData() { return runDevDataAction("/dev/reset-all-data"); }
function clearAllData() { return runDevDataAction("/dev/clear-all-data"); }
const defaultPreset = presets.availability;
"""
        return Response(script, media_type="text/javascript")

    @app.get("/equipment-types")
    def list_equipment_types(_: RequireRead) -> dict[str, list[dict[str, Any]]]:
        return {"equipmentTypes": store.list_equipment_types()}

    @app.post("/equipment-types", status_code=status.HTTP_201_CREATED)
    def create_equipment_type(
        body: EquipmentTypeCreate, request: Request, context: RequireModify
    ) -> dict[str, Any]:
        return store.create_equipment_type(body, store.actor_for(request, context))

    @app.put("/equipment-types/{code}")
    def update_equipment_type(
        code: str, body: EquipmentTypeUpdate, request: Request, context: RequireModify
    ) -> dict[str, Any]:
        return store.update_equipment_type(code, body, store.actor_for(request, context))

    @app.post("/containers", status_code=status.HTTP_201_CREATED)
    def register_container(
        body: ContainerCreate, request: Request, context: RequireModify
    ) -> dict[str, Any]:
        return store.register_container(body, store.actor_for(request, context))

    @app.get("/containers")
    def list_containers(
        _: RequireRead,
        type: str | None = None,
        equipmentType: str | None = None,
        status: str | None = None,
        depot: str | None = None,
        depotCode: str | None = None,
        currentDepot: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            "containers": store.list_containers(
                equipmentType or type, status, currentDepot or depotCode or depot
            )
        }

    @app.get("/containers/{container_id}")
    def get_container(container_id: str, _: RequireRead) -> dict[str, Any]:
        return store.get_container(container_id)

    @app.patch("/containers/{container_id}/status")
    def set_container_status(
        container_id: str, body: ContainerStatusUpdate, request: Request, context: RequireModify
    ) -> dict[str, Any]:
        return store.set_container_status(
            container_id, body.status, store.actor_for(request, context)
        )

    @app.get("/availability")
    def availability(
        _: RequireRead, depotCode: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        return {"availability": store.availability(depotCode)}

    @app.post("/reservations", status_code=status.HTTP_201_CREATED)
    def create_reservation(
        body: ReservationCreate, request: Request, context: RequireModify
    ) -> dict[str, Any]:
        return store.create_reservation(body, store.actor_for(request, context))

    @app.delete("/reservations/{booking_reference}")
    def release_reservation(
        booking_reference: str, request: Request, context: RequireModify
    ) -> dict[str, Any]:
        return store.release_reservation(booking_reference, store.actor_for(request, context))

    @app.post("/containers/{container_id}/pickup")
    def pickup(container_id: str, request: Request, context: RequireModify) -> dict[str, Any]:
        return store.pickup(container_id, store.actor_for(request, context))

    @app.post("/containers/{container_id}/return")
    def return_container(
        container_id: str, request: Request, context: RequireModify
    ) -> dict[str, Any]:
        return store.return_container(container_id, store.actor_for(request, context))

    @app.post("/events")
    def events(body: EventIn, request: Request, context: RequireModify) -> dict[str, Any]:
        return store.process_event(body, store.actor_for(request, context))

    @app.post("/dev/reset-all-data")
    def reset_all_data(_: Request, context: RequireModify) -> dict[str, bool]:
        if store.config.environment == "production":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        store.clear(seed=True)
        return {"reset": True, "seeded": True}

    @app.post("/dev/clear-all-data")
    def clear_all_data(_: Request, context: RequireModify) -> dict[str, bool]:
        if store.config.environment == "production":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        store.clear(seed=False)
        return {"reset": True, "seeded": False}

    @app.post("/dev/generate-token", status_code=status.HTTP_201_CREATED)
    def generate_token(body: DevTokenRequest) -> dict[str, Any]:
        if store.config.environment == "production":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
        if not body.subject.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "token subject is required")
        token = make_token(body.subject, body.scopes, body.role)
        response: dict[str, Any] = {
            "token": token,
            "subject": body.subject,
            "scopes": body.scopes,
        }
        if body.role is not None:
            response["role"] = body.role
        return response

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title="Equipments Service API",
            version=__version__,
            routes=app.routes,
            openapi_version="3.1.0",
        )
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["bearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
        }
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


def run() -> None:
    uvicorn.run("equipments_clone.main:create_app", factory=True, host="0.0.0.0", port=3000)


if __name__ == "__main__":
    run()
