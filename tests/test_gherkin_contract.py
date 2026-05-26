from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from equipments_clone import __version__

DEFAULT_SECRET = "equipments-dev-secret"
DEFAULT_ISSUER = "platform-auth"
DEFAULT_AUDIENCE = "equipments-service"

scenarios("features")


@dataclass
class ScenarioState:
    client: TestClient | None = None
    latest_response: Any | None = None
    latest_generated_token: str | None = None
    latest_container_id: str | None = None
    latest_reservation: dict[str, Any] | None = None
    latest_metadata: dict[str, Any] | None = None
    storage_path: Path | None = None
    monkeypatch: pytest.MonkeyPatch | None = None
    tmp_path: Path | None = None
    clients: list[TestClient] = field(default_factory=list)

    def require_client(self) -> TestClient:
        if self.client is None:
            raise AssertionError("service has not been started")
        return self.client

    def response(self) -> Any:
        if self.latest_response is None:
            raise AssertionError("no latest response")
        return self.latest_response


@pytest.fixture()
def state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[ScenarioState]:
    scenario_state = ScenarioState(monkeypatch=monkeypatch, tmp_path=tmp_path)
    yield scenario_state
    for client in scenario_state.clients:
        client.close()


def start_service(
    state: ScenarioState,
    *,
    seeded: bool,
    sqlite: bool = False,
    reuse_storage: bool = False,
) -> None:
    assert state.monkeypatch is not None
    state.monkeypatch.setenv("APP_ENV", "development")
    state.monkeypatch.setenv("AUTH_JWT_SECRET", DEFAULT_SECRET)
    state.monkeypatch.setenv("AUTH_JWT_ISSUER", DEFAULT_ISSUER)
    state.monkeypatch.setenv("AUTH_JWT_AUDIENCE", DEFAULT_AUDIENCE)
    state.monkeypatch.delenv("NODE_ENV", raising=False)

    if sqlite:
        assert state.tmp_path is not None
        if state.storage_path is None:
            state.storage_path = state.tmp_path / "equipments-gherkin.sqlite"
        state.monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
        state.monkeypatch.setenv("STORAGE_SQLITE_PATH", str(state.storage_path))
        state.monkeypatch.setenv("STORAGE_SQLITE_EMPTY_ON_FIRST_BOOT", "true")
    else:
        state.monkeypatch.delenv("STORAGE_BACKEND", raising=False)
        state.monkeypatch.delenv("STORAGE_SQLITE_PATH", raising=False)
        state.monkeypatch.delenv("STORAGE_DB_PATH", raising=False)
        state.monkeypatch.delenv("STORAGE_SQLITE_EMPTY_ON_FIRST_BOOT", raising=False)

    if reuse_storage:
        state.monkeypatch.setenv("STORAGE_SQLITE_EMPTY_ON_FIRST_BOOT", "true")

    from equipments_clone.main import create_app

    client = TestClient(create_app())
    state.clients.append(client)
    state.client = client

    if not seeded and not sqlite:
        response = client.post("/dev/clear-all-data", headers=modify_headers())
        assert response.status_code == 200, response.text


def make_token(
    *,
    secret: str = DEFAULT_SECRET,
    issuer: str = DEFAULT_ISSUER,
    audience: str | list[str] = DEFAULT_AUDIENCE,
    subject: str = "gherkin-user",
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


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def read_headers() -> dict[str, str]:
    return bearer(make_token(scopes=["equipments:read"]))


def modify_headers() -> dict[str, str]:
    return bearer(make_token(scopes=["equipments:read", "equipments:modify"]))


def admin_headers() -> dict[str, str]:
    return bearer(make_token(scopes=[], role="admin"))


def request_with_auth(
    state: ScenarioState,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    follow_redirects: bool = True,
) -> Any:
    response = state.require_client().request(
        method,
        path,
        headers=headers,
        json=json,
        follow_redirects=follow_redirects,
    )
    state.latest_response = response
    return response


def body(state: ScenarioState) -> dict[str, Any]:
    response_body = state.response().json()
    if not isinstance(response_body, dict):
        raise AssertionError(f"expected object response, got {response_body!r}")
    return response_body


def latest_error(state: ScenarioState) -> str:
    response_body = body(state)
    return str(response_body.get("error", response_body.get("detail", response_body)))


def availability_rows(state: ScenarioState, depot: str) -> list[dict[str, Any]]:
    response = request_with_auth(
        state,
        "GET",
        f"/availability?depotCode={depot}",
        headers=read_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()["availability"]


def container_status(state: ScenarioState, container_id: str) -> str:
    response = request_with_auth(
        state,
        "GET",
        f"/containers/{container_id}",
        headers=read_headers(),
    )
    assert response.status_code == 200, response.text
    return str(response.json()["status"])


def latest_container_id(state: ScenarioState) -> str:
    if state.latest_container_id:
        return state.latest_container_id
    if state.latest_reservation and state.latest_reservation["assignedContainers"]:
        return str(state.latest_reservation["assignedContainers"][0]["containerId"])
    raise AssertionError("no latest container id")


@given("the seeded equipments service is running")
def seeded_service(state: ScenarioState) -> None:
    start_service(state, seeded=True)


@given("the equipments service starts from an empty sqlite database")
def empty_sqlite_service(state: ScenarioState) -> None:
    start_service(state, seeded=False, sqlite=True)


@when("I restart the service with the same runtime storage and no seeded data")
def restart_same_storage(state: ScenarioState) -> None:
    start_service(state, seeded=False, sqlite=True, reuse_storage=True)


@when(parsers.parse('I request GET "{path}" without a bearer token'))
def get_without_token(state: ScenarioState, path: str) -> None:
    request_with_auth(state, "GET", path, follow_redirects=False)


@when(parsers.parse('I request GET "{path}" with a read bearer token'))
def get_with_read_token(state: ScenarioState, path: str) -> None:
    request_with_auth(state, "GET", path, headers=read_headers(), follow_redirects=False)


@when(parsers.parse('I request GET "{path}" with the latest generated bearer token'))
def get_with_generated_token(state: ScenarioState, path: str) -> None:
    if state.latest_generated_token is None:
        raise AssertionError("no generated token")
    request_with_auth(
        state,
        "GET",
        path,
        headers=bearer(state.latest_generated_token),
        follow_redirects=False,
    )


@when(parsers.parse('I request GET "{path}" with an admin bearer token without equipment scopes'))
def get_with_admin_token(state: ScenarioState, path: str) -> None:
    request_with_auth(state, "GET", path, headers=admin_headers(), follow_redirects=False)


@when(
    parsers.parse(
        'I request GET "{path}" with a Users Service admin bearer token for audience "{audience}"'
    )
)
def get_with_wrong_audience_admin(state: ScenarioState, path: str, audience: str) -> None:
    request_with_auth(
        state,
        "GET",
        path,
        headers=bearer(make_token(scopes=[], role="admin", audience=audience)),
    )


@when(
    parsers.parse(
        'I request GET "{path}" with a Users Service admin bearer token '
        'from issuer "{issuer}"'
    )
)
def get_with_wrong_issuer_admin(state: ScenarioState, path: str, issuer: str) -> None:
    request_with_auth(
        state,
        "GET",
        path,
        headers=bearer(make_token(scopes=[], role="admin", issuer=issuer)),
    )


@when(parsers.parse('I request GET "{path}" with an expired Users Service admin bearer token'))
def get_with_expired_admin(state: ScenarioState, path: str) -> None:
    request_with_auth(
        state,
        "GET",
        path,
        headers=bearer(make_token(scopes=[], role="admin", expires_delta=timedelta(seconds=-1))),
    )


@when(
    parsers.parse(
        'I request GET "{path}" with a Users Service admin bearer token '
        "that has an invalid signature"
    )
)
def get_with_bad_signature_admin(state: ScenarioState, path: str) -> None:
    request_with_auth(
        state,
        "GET",
        path,
        headers=bearer(make_token(scopes=[], role="admin", secret="bad-secret")),
    )


@when(
    parsers.parse(
        'I request GET "{path}" with a bearer token role "{role}" '
        "and no equipment scopes"
    )
)
def get_with_exact_role_check(state: ScenarioState, path: str, role: str) -> None:
    request_with_auth(state, "GET", path, headers=bearer(make_token(scopes=[], role=role)))


@when(
    parsers.parse(
        'I create equipment type "{code}" described as "{description}" '
        'with nominal length "{nominal}" and max payload {payload:d}'
    )
)
@when(
    parsers.parse(
        'I try to create equipment type "{code}" described as "{description}" '
        'with nominal length "{nominal}" and max payload {payload:d}'
    )
)
def create_equipment_type(
    state: ScenarioState, code: str, description: str, nominal: str, payload: int
) -> None:
    response = request_with_auth(
        state,
        "POST",
        "/equipment-types",
        headers=modify_headers(),
        json={
            "code": code,
            "description": description,
            "nominalLength": nominal,
            "maxPayloadKg": payload,
        },
    )
    if response.status_code in {200, 201}:
        state.latest_metadata = {
            "createdByUserId": response.json().get("createdByUserId"),
            "lastModifiedByUserId": response.json().get("lastModifiedByUserId"),
        }


@when(parsers.parse('I update equipment type "{code}" description to "{description}"'))
@when(parsers.parse('I try to update equipment type "{code}" description to "{description}"'))
def update_equipment_type(state: ScenarioState, code: str, description: str) -> None:
    request_with_auth(
        state,
        "PUT",
        f"/equipment-types/{code}",
        headers=modify_headers(),
        json={"description": description},
    )


@when(
    parsers.parse(
        'I register container "{number}" of type "{equipment_type}" at depot "{depot}"'
    )
)
def register_container(state: ScenarioState, number: str, equipment_type: str, depot: str) -> None:
    create_container_with_headers(state, number, equipment_type, depot, modify_headers())


@when(
    parsers.parse(
        'I try to register container "{number}" of type "{equipment_type}" '
        'at depot "{depot}" with a read bearer token'
    )
)
def try_register_container_with_read_token(
    state: ScenarioState, number: str, equipment_type: str, depot: str
) -> None:
    create_container_with_headers(state, number, equipment_type, depot, read_headers())


@when(
    parsers.parse(
        'I register container "{number}" of type "{equipment_type}" '
        'at depot "{depot}" with an admin bearer token without equipment scopes'
    )
)
def register_container_with_admin(
    state: ScenarioState, number: str, equipment_type: str, depot: str
) -> None:
    create_container_with_headers(state, number, equipment_type, depot, admin_headers())


def create_container_with_headers(
    state: ScenarioState,
    number: str,
    equipment_type: str,
    depot: str,
    headers: dict[str, str],
) -> None:
    response = request_with_auth(
        state,
        "POST",
        "/containers",
        headers=headers,
        json={
            "containerNumber": number,
            "equipmentType": equipment_type,
            "currentDepot": depot,
        },
    )
    if response.status_code in {200, 201}:
        state.latest_container_id = str(response.json()["id"])


@when(
    parsers.parse(
        'I list containers with type "{equipment_type}" status "{status}" depot "{depot}"'
    )
)
def list_containers(state: ScenarioState, equipment_type: str, status: str, depot: str) -> None:
    request_with_auth(
        state,
        "GET",
        f"/containers?type={equipment_type}&status={status}&depot={depot}",
        headers=read_headers(),
    )


@when("I fetch the latest container")
def fetch_latest_container(state: ScenarioState) -> None:
    request_with_auth(
        state,
        "GET",
        f"/containers/{latest_container_id(state)}",
        headers=read_headers(),
    )


@when(parsers.parse('I manually set the latest container status to "{status}"'))
def set_latest_container_status(state: ScenarioState, status: str) -> None:
    request_with_auth(
        state,
        "PATCH",
        f"/containers/{latest_container_id(state)}/status",
        headers=modify_headers(),
        json={"status": status},
    )


@when(
    parsers.parse(
        'I reserve {quantity:d} units of "{equipment_type}" at depot "{depot}" '
        'for booking "{booking}"'
    )
)
@when(
    parsers.parse(
        'I try to reserve {quantity:d} units of "{equipment_type}" at depot "{depot}" '
        'for booking "{booking}"'
    )
)
def reserve_equipment(
    state: ScenarioState, quantity: int, equipment_type: str, depot: str, booking: str
) -> None:
    response = request_with_auth(
        state,
        "POST",
        "/reservations",
        headers=modify_headers(),
        json={
            "bookingReference": booking,
            "originDepot": depot,
            "equipment": [{"type": equipment_type, "quantity": quantity}],
        },
    )
    if response.status_code in {200, 201}:
        state.latest_reservation = response.json()
        assigned = state.latest_reservation["assignedContainers"]
        if assigned:
            state.latest_container_id = str(assigned[0]["containerId"])


@when("I pick up the latest reserved container")
@when("I try to pick up the latest reserved container")
def pickup_latest_container(state: ScenarioState) -> None:
    request_with_auth(
        state,
        "POST",
        f"/containers/{latest_container_id(state)}/pickup",
        headers=modify_headers(),
    )


@when("I return the latest container")
def return_latest_container(state: ScenarioState) -> None:
    request_with_auth(
        state,
        "POST",
        f"/containers/{latest_container_id(state)}/return",
        headers=modify_headers(),
    )


@when(parsers.parse('I release booking "{booking}"'))
def release_booking(state: ScenarioState, booking: str) -> None:
    request_with_auth(
        state,
        "DELETE",
        f"/reservations/{booking}",
        headers=modify_headers(),
    )


@when(parsers.parse('I receive a "{event_type}" event for booking "{booking}"'))
def receive_event(state: ScenarioState, event_type: str, booking: str) -> None:
    request_with_auth(
        state,
        "POST",
        "/events",
        headers=modify_headers(),
        json={"eventType": event_type, "payload": {"bookingReference": booking}},
    )


@when(
    parsers.parse(
        'I generate a development bearer token for subject "{subject}" with read scope'
    )
)
def generate_read_token(state: ScenarioState, subject: str) -> None:
    response = request_with_auth(
        state,
        "POST",
        "/dev/generate-token",
        json={"subject": subject, "scopes": ["equipments:read"]},
    )
    if response.status_code == 201:
        state.latest_generated_token = str(response.json()["token"])


@then(parsers.parse("the latest response status is {status_code:d}"))
def latest_status(state: ScenarioState, status_code: int) -> None:
    assert state.response().status_code == status_code, state.response().text


@then(parsers.parse('the latest JSON response has field "{field_name}" equal to "{value}"'))
def json_field_equals(state: ScenarioState, field_name: str, value: str) -> None:
    assert str(body(state)[field_name]) == value


@then(
    parsers.parse(
        'the latest JSON response has boolean field "{field_name}" equal to {value}'
    )
)
def json_bool_field_equals(state: ScenarioState, field_name: str, value: str) -> None:
    assert body(state)[field_name] is (value == "true")


@then(
    parsers.parse(
        'the latest JSON response has field "{field_name}" equal to the service version'
    )
)
def json_field_is_version(state: ScenarioState, field_name: str) -> None:
    assert body(state)[field_name] == __version__


@then(parsers.parse('the latest response content type starts with "{prefix}"'))
def content_type_starts_with(state: ScenarioState, prefix: str) -> None:
    assert state.response().headers["content-type"].startswith(prefix)


@then(parsers.parse('the latest OpenAPI response title is "{title}"'))
def openapi_title(state: ScenarioState, title: str) -> None:
    assert body(state)["info"]["title"] == title


@then(parsers.parse('the latest OpenAPI response exposes path "{path}"'))
def openapi_exposes_path(state: ScenarioState, path: str) -> None:
    assert path in body(state)["paths"]


@then(
    parsers.parse(
        'the latest OpenAPI bearerAuth security scheme has type "{scheme_type}" '
        'and scheme "{scheme}"'
    )
)
def openapi_bearer_scheme(state: ScenarioState, scheme_type: str, scheme: str) -> None:
    assert body(state)["components"]["securitySchemes"]["bearerAuth"] == {
        "type": scheme_type,
        "scheme": scheme,
    }


@then(parsers.parse('the latest response redirects to "{path}"'))
def response_redirects_to(state: ScenarioState, path: str) -> None:
    assert state.response().headers["location"] == path


@then(parsers.parse('the latest error is "{message}"'))
def latest_error_is(state: ScenarioState, message: str) -> None:
    assert latest_error(state) == message


@then(parsers.parse('the latest error contains "{message}"'))
def latest_error_contains(state: ScenarioState, message: str) -> None:
    assert message in latest_error(state)


@then(parsers.parse("the equipment type catalog contains {count:d} entries"))
def equipment_type_count(state: ScenarioState, count: int) -> None:
    response = request_with_auth(state, "GET", "/equipment-types", headers=read_headers())
    assert response.status_code == 200, response.text
    assert len(response.json()["equipmentTypes"]) == count


@then("the equipment type catalog is empty")
def equipment_type_empty(state: ScenarioState) -> None:
    equipment_type_count(state, 0)


@then(
    parsers.parse('the equipment type catalog includes "{code}" described as "{description}"')
)
def equipment_catalog_includes(state: ScenarioState, code: str, description: str) -> None:
    response = request_with_auth(state, "GET", "/equipment-types", headers=read_headers())
    assert response.status_code == 200, response.text
    matches = [
        item
        for item in response.json()["equipmentTypes"]
        if item["code"] == code and item["description"] == description
    ]
    assert matches


@then(parsers.parse("the container inventory contains {count:d} entries"))
def container_count(state: ScenarioState, count: int) -> None:
    response = request_with_auth(state, "GET", "/containers", headers=read_headers())
    assert response.status_code == 200, response.text
    assert len(response.json()["containers"]) == count


@then("the container inventory is empty")
def container_inventory_empty(state: ScenarioState) -> None:
    container_count(state, 0)


@then(parsers.parse('availability at depot "{depot}" shows {count:d} units of "{equipment_type}"'))
def availability_shows(state: ScenarioState, depot: str, count: int, equipment_type: str) -> None:
    rows = availability_rows(state, depot)
    matches = [row for row in rows if row["equipmentType"] == equipment_type]
    assert matches, rows
    assert matches[0]["availableCount"] == count


@then(parsers.parse('availability at depot "{depot}" is empty'))
def availability_empty(state: ScenarioState, depot: str) -> None:
    assert availability_rows(state, depot) == []


@then(parsers.parse('the latest container status is "{status}"'))
def latest_container_status_is(state: ScenarioState, status: str) -> None:
    if state.response().status_code == 200 and "status" in body(state):
        assert body(state)["status"] == status
        return
    assert container_status(state, latest_container_id(state)) == status


@then(parsers.parse('the latest container list includes container "{container_number}"'))
def latest_container_list_includes(state: ScenarioState, container_number: str) -> None:
    assert any(
        item["containerNumber"] == container_number
        for item in body(state)["containers"]
    )


@then("the latest container booking reference is null")
def latest_container_booking_reference_null(state: ScenarioState) -> None:
    response = request_with_auth(
        state,
        "GET",
        f"/containers/{latest_container_id(state)}",
        headers=read_headers(),
    )
    assert response.json()["bookingReference"] is None


@then(parsers.parse("the latest reservation assigned {count:d} containers"))
def latest_reservation_assigned_count(state: ScenarioState, count: int) -> None:
    assert len(body(state)["assignedContainers"]) == count


@then("the latest reservation has an assigned container")
def latest_reservation_has_assigned_container(state: ScenarioState) -> None:
    assert body(state)["assignedContainers"]


@then(parsers.parse('the latest reservation status is "{status}"'))
def latest_reservation_status(state: ScenarioState, status: str) -> None:
    assert body(state)["status"] == status


@then(parsers.parse('the latest reservation release status is "{status}"'))
def latest_reservation_release_status(state: ScenarioState, status: str) -> None:
    assert body(state)["status"] == status


@then(parsers.parse('all containers assigned to the latest reservation have status "{status}"'))
def assigned_containers_have_status(state: ScenarioState, status: str) -> None:
    assigned = body(state)["assignedContainers"]
    assert assigned
    for item in assigned:
        assert container_status(state, str(item["containerId"])) == status


@then("the latest response includes a generated bearer token")
def response_includes_token(state: ScenarioState) -> None:
    assert isinstance(body(state).get("token"), str)
    assert body(state)["token"]


@then(
    parsers.parse(
        'the latest JSON response has string array field "{field_name}" '
        'containing exactly "{value}"'
    )
)
def string_array_exactly(state: ScenarioState, field_name: str, value: str) -> None:
    assert body(state)[field_name] == [value]


@then("the latest JSON response has persisted local user metadata")
def persisted_local_user_metadata(state: ScenarioState) -> None:
    payload = body(state)
    state.latest_metadata = {
        "createdByUserId": payload["createdByUserId"],
        "lastModifiedByUserId": payload["lastModifiedByUserId"],
    }
    assert payload["createdByUserId"]
    assert payload["lastModifiedByUserId"]


@then(parsers.parse('equipment type "{code}" still has the same local user metadata'))
def equipment_still_has_metadata(state: ScenarioState, code: str) -> None:
    response = request_with_auth(state, "GET", "/equipment-types", headers=read_headers())
    assert response.status_code == 200, response.text
    match = next(item for item in response.json()["equipmentTypes"] if item["code"] == code)
    assert state.latest_metadata is not None
    assert match["createdByUserId"] == state.latest_metadata["createdByUserId"]
    assert match["lastModifiedByUserId"] == state.latest_metadata["lastModifiedByUserId"]
