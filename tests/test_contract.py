from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient

DEFAULT_SECRET = "equipments-dev-secret"
DEFAULT_ISSUER = "platform-auth"
DEFAULT_AUDIENCE = "equipments-service"


def token(
    *,
    secret: str = DEFAULT_SECRET,
    issuer: str = DEFAULT_ISSUER,
    audience: str | list[str] = DEFAULT_AUDIENCE,
    subject: str = "contract-user",
    scopes: list[str] | None = None,
    role: str | None = None,
    expires_delta: timedelta = timedelta(hours=1),
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + expires_delta,
    }
    if scopes is not None:
        payload["scope"] = " ".join(scopes)
    if role is not None:
        payload["role"] = role
    return jwt.encode(payload, secret, algorithm="HS256")


def auth_header(jwt_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt_token}"}


def read_headers(**kwargs: Any) -> dict[str, str]:
    return auth_header(token(scopes=["equipments:read"], **kwargs))


def modify_headers(**kwargs: Any) -> dict[str, str]:
    return auth_header(token(scopes=["equipments:read", "equipments:modify"], **kwargs))


def admin_headers(**kwargs: Any) -> dict[str, str]:
    return auth_header(token(scopes=[], role="admin", **kwargs))


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("AUTH_JWT_SECRET", DEFAULT_SECRET)
    monkeypatch.setenv("AUTH_JWT_ISSUER", DEFAULT_ISSUER)
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", DEFAULT_AUDIENCE)
    monkeypatch.delenv("NODE_ENV", raising=False)
    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("STORAGE_SQLITE_PATH", raising=False)
    monkeypatch.delenv("STORAGE_DB_PATH", raising=False)

    from equipments_clone.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def error(response: Any) -> str:
    body = response.json()
    return str(body.get("error", body.get("detail", body)))


def by_equipment_type(availability: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["equipmentType"]: row for row in availability}


def reserve_one(client: TestClient, booking: str, equipment_type: str = "20FT") -> dict[str, Any]:
    response = client.post(
        "/reservations",
        json={
            "bookingReference": booking,
            "originDepot": "CNSHA-01",
            "equipment": [{"type": equipment_type, "quantity": 1}],
        },
        headers=modify_headers(),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_public_routes_and_openapi_contract(client: TestClient) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert "version" in health.json()

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    schema = openapi.json()
    assert schema["openapi"] == "3.1.0"
    assert schema["info"]["title"] == "Equipments Service API"
    assert "/availability" in schema["paths"]
    assert "/reservations" in schema["paths"]
    assert "/events" in schema["paths"]
    assert schema["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }

    root = client.get("/", follow_redirects=False)
    assert root.status_code == 302
    assert root.headers["location"] == "/playground"

    playground = client.get("/playground")
    assert playground.status_code == 200
    assert "Equipments API Playground" in playground.text
    assert "Active Backend" in playground.text
    assert "Bearer token" in playground.text
    assert "equipments:read" in playground.text

    css = client.get("/playground/playground.css")
    assert css.status_code == 200
    assert ".backend-chip" in css.text

    script = client.get("/playground/playground.js")
    assert script.status_code == 200
    assert "const presets =" in script.text
    assert "function generateToken(" in script.text
    assert "/dev/generate-token" in script.text


def test_protected_routes_require_valid_bearer_token_and_scope(client: TestClient) -> None:
    missing = client.get("/equipment-types")
    assert missing.status_code == 401
    assert error(missing) == "missing bearer token"

    read_only_write = client.post(
        "/containers",
        json={
            "containerNumber": "READ1111111",
            "equipmentType": "20FT",
            "currentDepot": "CNSHA-01",
        },
        headers=read_headers(),
    )
    assert read_only_write.status_code == 403
    assert error(read_only_write) == "missing required scope equipments:modify"

    no_read_scope = client.get(
        "/equipment-types", headers=auth_header(token(scopes=["equipments:modify"]))
    )
    assert no_read_scope.status_code == 403
    assert error(no_read_scope) == "missing required scope equipments:read"

    wrong_audience = client.get(
        "/equipment-types",
        headers=read_headers(audience="wrong-audience"),
    )
    assert wrong_audience.status_code == 401
    assert error(wrong_audience) == "bearer token audience is invalid"

    wrong_issuer = client.get(
        "/equipment-types",
        headers=read_headers(issuer="users-service"),
    )
    assert wrong_issuer.status_code == 401
    assert error(wrong_issuer) == "bearer token issuer is invalid"

    expired = client.get(
        "/equipment-types",
        headers=read_headers(expires_delta=timedelta(seconds=-1)),
    )
    assert expired.status_code == 401
    assert error(expired) == "bearer token is expired"

    invalid_signature = client.get(
        "/equipment-types",
        headers=auth_header(token(scopes=["equipments:read"], secret="wrong-secret")),
    )
    assert invalid_signature.status_code == 401
    assert error(invalid_signature) == "invalid bearer token signature"


def test_admin_role_is_exact_and_bypasses_scopes(client: TestClient) -> None:
    read = client.get("/equipment-types", headers=admin_headers())
    assert read.status_code == 200

    created = client.post(
        "/containers",
        json={
            "containerNumber": "ADMG1111111",
            "equipmentType": "20FT",
            "currentDepot": "CNSHA-01",
        },
        headers=admin_headers(),
    )
    assert created.status_code == 201

    for role in ["Admin", "administrator"]:
        denied = client.get(
            "/equipment-types",
            headers=auth_header(token(scopes=[], role=role)),
        )
        assert denied.status_code == 403
        assert error(denied) == "missing required scope equipments:read"


def test_inventory_catalog_container_and_availability_contract(client: TestClient) -> None:
    types = client.get("/equipment-types", headers=read_headers())
    assert types.status_code == 200
    catalog = types.json()["equipmentTypes"]
    assert {item["code"] for item in catalog} == {"20FT", "40FT", "40HC", "20RF", "40RF"}

    created_type = client.post(
        "/equipment-types",
        json={
            "code": "45HC",
            "description": "45-foot High Cube",
            "nominalLength": "45'",
            "maxPayloadKg": 29500,
        },
        headers=modify_headers(),
    )
    assert created_type.status_code == 201
    assert created_type.json()["code"] == "45HC"
    assert created_type.json()["createdByUserId"] == created_type.json()["lastModifiedByUserId"]

    duplicate = client.post(
        "/equipment-types",
        json={
            "code": "20FT",
            "description": "Duplicate",
            "nominalLength": "20'",
            "maxPayloadKg": 1,
        },
        headers=modify_headers(),
    )
    assert duplicate.status_code == 409
    assert "equipment type 20FT already exists" in error(duplicate)

    updated_type = client.put(
        "/equipment-types/45hc",
        json={"description": "45-foot High Cube Updated"},
        headers=modify_headers(),
    )
    assert updated_type.status_code == 200
    assert updated_type.json()["description"] == "45-foot High Cube Updated"

    missing_type = client.put(
        "/equipment-types/DOES-NOT-EXIST",
        json={"description": "nope"},
        headers=modify_headers(),
    )
    assert missing_type.status_code == 404
    assert "equipment type DOES-NOT-EXIST not found" in error(missing_type)

    container = client.post(
        "/containers",
        json={
            "containerNumber": "CONU8888888",
            "equipmentType": "20FT",
            "currentDepot": "NLRTM-01",
        },
        headers=modify_headers(),
    )
    assert container.status_code == 201
    container_body = container.json()
    assert container_body["status"] == "AVAILABLE"
    container_id = container_body["id"]

    filtered = client.get(
        "/containers",
        params={"type": "20FT", "status": "AVAILABLE", "depot": "NLRTM-01"},
        headers=read_headers(),
    )
    assert filtered.status_code == 200
    assert any(item["containerNumber"] == "CONU8888888" for item in filtered.json()["containers"])

    fetched = client.get(f"/containers/{container_id}", headers=read_headers())
    assert fetched.status_code == 200
    assert fetched.json()["containerNumber"] == "CONU8888888"

    patched = client.patch(
        f"/containers/{container_id}/status",
        json={"status": "IN_TRANSIT"},
        headers=modify_headers(),
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "IN_TRANSIT"

    unknown_type = client.post(
        "/containers",
        json={
            "containerNumber": "CONU7777777",
            "equipmentType": "NOPE",
            "currentDepot": "CNSHA-01",
        },
        headers=modify_headers(),
    )
    assert unknown_type.status_code == 400
    assert "unknown equipment type NOPE" in error(unknown_type)

    invalid_status = client.patch(
        f"/containers/{container_id}/status",
        json={"status": "BROKEN"},
        headers=modify_headers(),
    )
    assert invalid_status.status_code == 400
    assert "invalid container status BROKEN" in error(invalid_status)

    availability = client.get(
        "/availability",
        params={"depotCode": "CNSHA-01"},
        headers=read_headers(),
    )
    assert availability.status_code == 200
    counts = by_equipment_type(availability.json()["availability"])
    assert counts["20FT"]["availableCount"] == 3
    assert counts["40FT"]["availableCount"] == 2
    assert counts["40HC"]["availableCount"] == 1


def test_reservations_lifecycle_and_events(client: TestClient) -> None:
    reservation = client.post(
        "/reservations",
        json={
            "bookingReference": "BKG-2026-00042",
            "originDepot": "CNSHA-01",
            "equipment": [{"type": "20FT", "quantity": 2}],
        },
        headers=modify_headers(),
    )
    assert reservation.status_code == 201
    body = reservation.json()
    assert body["status"] == "ACTIVE"
    assert len(body["assignedContainers"]) == 2
    first_container_id = body["assignedContainers"][0]["containerId"]

    after_reserve = client.get(
        "/availability",
        params={"depotCode": "CNSHA-01"},
        headers=read_headers(),
    )
    assert by_equipment_type(after_reserve.json()["availability"])["20FT"]["availableCount"] == 1

    duplicate = client.post(
        "/reservations",
        json={
            "bookingReference": "BKG-2026-00042",
            "originDepot": "CNSHA-01",
            "equipment": [{"type": "20FT", "quantity": 1}],
        },
        headers=modify_headers(),
    )
    assert duplicate.status_code == 409
    assert "booking BKG-2026-00042 already has a reservation" in error(duplicate)

    insufficient = client.post(
        "/reservations",
        json={
            "bookingReference": "BKG-OVER-ASK",
            "originDepot": "CNSHA-01",
            "equipment": [{"type": "40HC", "quantity": 2}],
        },
        headers=modify_headers(),
    )
    assert insufficient.status_code == 409
    assert "insufficient available 40HC at depot CNSHA-01" in error(insufficient)

    picked_up = client.post(
        f"/containers/{first_container_id}/pickup",
        headers=modify_headers(),
    )
    assert picked_up.status_code == 200
    assert picked_up.json()["status"] == "DISPATCHED"

    release_after_pickup = client.delete(
        "/reservations/BKG-2026-00042",
        headers=modify_headers(),
    )
    assert release_after_pickup.status_code == 409
    assert "cannot be released after dispatch" in error(release_after_pickup)

    returned = client.post(
        f"/containers/{first_container_id}/return",
        headers=modify_headers(),
    )
    assert returned.status_code == 200
    assert returned.json()["status"] == "AVAILABLE"
    assert returned.json()["bookingReference"] is None

    release_candidate = reserve_one(client, "BKG-DELETE-1", "40FT")
    release = client.delete("/reservations/BKG-DELETE-1", headers=modify_headers())
    assert release.status_code == 200
    assert release.json()["status"] == "RELEASED"
    assert release_candidate["assignedContainers"][0]["containerId"] in release.json()["containers"]

    cancelled = reserve_one(client, "BKG-CANCEL-1", "20FT")
    event = client.post(
        "/events",
        json={"eventType": "booking.cancelled", "payload": {"bookingReference": "BKG-CANCEL-1"}},
        headers=modify_headers(),
    )
    assert event.status_code == 200
    assert event.json()["processed"] is True
    cancelled_container = client.get(
        f"/containers/{cancelled['assignedContainers'][0]['containerId']}",
        headers=read_headers(),
    )
    assert cancelled_container.json()["status"] == "AVAILABLE"

    completed = reserve_one(client, "BKG-COMPLETE-1", "20FT")
    completed_container_id = completed["assignedContainers"][0]["containerId"]
    client.post(f"/containers/{completed_container_id}/pickup", headers=modify_headers())
    completion = client.post(
        "/events",
        json={"eventType": "booking.completed", "payload": {"bookingReference": "BKG-COMPLETE-1"}},
        headers=modify_headers(),
    )
    assert completion.status_code == 200
    assert completion.json()["processed"] is True
    completed_container = client.get(
        f"/containers/{completed_container_id}", headers=read_headers()
    )
    assert completed_container.json()["status"] == "AVAILABLE"

    unknown_completion = client.post(
        "/events",
        json={"eventType": "booking.completed", "payload": {"bookingReference": "BKG-NOT-FOUND"}},
        headers=modify_headers(),
    )
    assert unknown_completion.status_code == 200
    assert unknown_completion.json()["processed"] is False


def test_audit_metadata_and_partial_caller_headers(client: TestClient) -> None:
    only_issuer = client.post(
        "/equipment-types",
        json={
            "code": "46PI",
            "description": "46-foot Partial Issuer",
            "nominalLength": "46'",
            "maxPayloadKg": 28600,
        },
        headers={**modify_headers(), "x-auth-issuer": "ops"},
    )
    assert only_issuer.status_code == 400
    assert error(only_issuer) == (
        "authenticated caller metadata requires both x-auth-issuer and x-auth-subject headers"
    )

    created = client.post(
        "/equipment-types",
        json={
            "code": "46AM",
            "description": "46-foot Audit Metadata",
            "nominalLength": "46'",
            "maxPayloadKg": 28600,
        },
        headers={
            **modify_headers(),
            "x-auth-issuer": "local-test",
            "x-auth-subject": "ops-create",
        },
    )
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["createdByUser"]["subject"] == "ops-create"
    assert created_body["lastModifiedByUser"]["subject"] == "ops-create"

    updated = client.put(
        "/equipment-types/46AM",
        json={"description": "46-foot Audit Metadata Updated"},
        headers={
            **modify_headers(),
            "x-auth-issuer": "local-test",
            "x-auth-subject": "ops-update",
        },
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["createdByUserId"] == created_body["createdByUserId"]
    assert updated_body["lastModifiedByUser"]["subject"] == "ops-update"

    client.app.state.store.audit_events.clear()
    read = client.get("/equipment-types", headers=read_headers())
    assert read.status_code == 200
    assert client.app.state.store.audit_events == []


def test_development_tools_and_production_gate(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = client.post(
        "/dev/generate-token",
        json={"subject": "playground-user", "scopes": ["equipments:read"]},
    )
    assert generated.status_code == 201
    token_value = generated.json()["token"]
    assert generated.json()["subject"] == "playground-user"
    assert generated.json()["scopes"] == ["equipments:read"]
    generated_read = client.get(
        "/availability?depotCode=CNSHA-01", headers=auth_header(token_value)
    )
    assert generated_read.status_code == 200

    admin_generated = client.post(
        "/dev/generate-token",
        json={"subject": "playground-admin", "role": "admin"},
    )
    assert admin_generated.status_code == 201
    assert admin_generated.json()["role"] == "admin"
    assert admin_generated.json()["scopes"] == []

    blank = client.post("/dev/generate-token", json={"subject": "   "})
    assert blank.status_code == 400
    assert error(blank) == "token subject is required"

    reset = client.post("/dev/reset-all-data", headers=modify_headers())
    assert reset.status_code == 200
    assert reset.json() == {"reset": True, "seeded": True}

    clear = client.post("/dev/clear-all-data", headers=modify_headers())
    assert clear.status_code == 200
    assert clear.json() == {"reset": True, "seeded": False}
    empty_types = client.get("/equipment-types", headers=read_headers())
    assert empty_types.json()["equipmentTypes"] == []

    monkeypatch.setenv("APP_ENV", "production")
    from equipments_clone.main import create_app

    with TestClient(create_app()) as production:
        playground = production.get("/playground")
        assert "Reset All Data" not in playground.text
        assert "Clear All Data" not in playground.text
        assert "unavailable outside development mode" in playground.text
        assert production.post(
            "/dev/generate-token", json={"subject": "playground-user"}
        ).status_code == 404
        assert production.post("/dev/reset-all-data", headers=modify_headers()).status_code == 404
        assert production.post("/dev/clear-all-data", headers=modify_headers()).status_code == 404


def test_runtime_config_and_sqlite_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    from equipments_clone.main import create_app, runtime_config_from_env

    assert runtime_config_from_env({}).backend == "memory"

    with pytest.raises(RuntimeError, match="STORAGE_DB_PATH is required"):
        runtime_config_from_env({"STORAGE_BACKEND": "db"})

    with pytest.raises(RuntimeError, match="STORAGE_SQLITE_PATH or STORAGE_DB_PATH is required"):
        runtime_config_from_env({"STORAGE_BACKEND": "sqlite"})

    with pytest.raises(RuntimeError, match="STORAGE_POSTGRES_URL is required"):
        runtime_config_from_env({"STORAGE_BACKEND": "postgres"})

    db_path = tmp_path / "equipments.sqlite"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("STORAGE_SQLITE_PATH", str(db_path))
    monkeypatch.setenv("STORAGE_SQLITE_EMPTY_ON_FIRST_BOOT", "true")

    with TestClient(create_app()) as first_client:
        empty_types = first_client.get(
            "/equipment-types", headers=read_headers()
        ).json()["equipmentTypes"]
        assert empty_types == []
        created = first_client.post(
            "/equipment-types",
            json={
                "code": "45OT",
                "description": "45-foot Open Top",
                "nominalLength": "45'",
                "maxPayloadKg": 28000,
            },
            headers=modify_headers(),
        )
        assert created.status_code == 201

    with TestClient(create_app()) as second_client:
        types = second_client.get(
            "/equipment-types", headers=read_headers()
        ).json()["equipmentTypes"]
        assert any(item["code"] == "45OT" for item in types)
