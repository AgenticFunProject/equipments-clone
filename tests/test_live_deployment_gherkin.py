from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

BASE_URL = os.environ.get("EQUIPMENTS_LIVE_BASE_URL", "").rstrip("/")
JWT_SECRET = os.environ.get("EQUIPMENTS_LIVE_JWT_SECRET", "")
JWT_ISSUER = os.environ.get("EQUIPMENTS_LIVE_JWT_ISSUER", "platform-auth")
JWT_AUDIENCE = os.environ.get("EQUIPMENTS_LIVE_JWT_AUDIENCE", "equipments-service")

pytestmark = pytest.mark.skipif(
    not BASE_URL or not JWT_SECRET,
    reason="set EQUIPMENTS_LIVE_BASE_URL and EQUIPMENTS_LIVE_JWT_SECRET to run live tests",
)

scenarios("live_features/live-deployment.feature")


@dataclass
class LiveState:
    suffix: str
    client: httpx.Client
    latest_response: httpx.Response | None = None
    latest_equipment_type: str | None = None
    latest_container_number: str | None = None
    latest_container_id: str | None = None
    latest_booking: str | None = None

    @property
    def depot(self) -> str:
        return f"LIVE-{self.suffix}"

    def response(self) -> httpx.Response:
        if self.latest_response is None:
            raise AssertionError("no latest response")
        return self.latest_response


@pytest.fixture()
def live_state() -> LiveState:
    suffix = uuid4().hex[:8].upper()
    with httpx.Client(base_url=BASE_URL, timeout=30.0, follow_redirects=False) as client:
        yield LiveState(suffix=suffix, client=client)


def make_token(
    *,
    audience: str | list[str] = JWT_AUDIENCE,
    issuer: str = JWT_ISSUER,
    secret: str = JWT_SECRET,
    scopes: list[str] | None = None,
    role: str | None = None,
    subject: str = "live-gherkin-user",
    expires_delta: timedelta = timedelta(hours=1),
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, object] = {
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


def request(
    live_state: LiveState,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, object] | None = None,
) -> httpx.Response:
    response = live_state.client.request(method, path, headers=headers, json=json)
    live_state.latest_response = response
    return response


def body(live_state: LiveState) -> dict[str, object]:
    payload = live_state.response().json()
    if not isinstance(payload, dict):
        raise AssertionError(f"expected object response, got {payload!r}")
    return payload


def latest_error(live_state: LiveState) -> str:
    payload = body(live_state)
    return str(payload.get("error", payload.get("detail", payload)))


def latest_equipment_type(live_state: LiveState) -> str:
    if live_state.latest_equipment_type is None:
        raise AssertionError("no latest equipment type")
    return live_state.latest_equipment_type


def latest_container_id(live_state: LiveState) -> str:
    if live_state.latest_container_id is None:
        raise AssertionError("no latest container id")
    return live_state.latest_container_id


def latest_booking(live_state: LiveState) -> str:
    if live_state.latest_booking is None:
        raise AssertionError("no latest booking")
    return live_state.latest_booking


def create_unique_equipment_type(live_state: LiveState) -> httpx.Response:
    code = f"L{live_state.suffix[:5]}"
    live_state.latest_equipment_type = code
    return request(
        live_state,
        "POST",
        "/equipment-types",
        headers=modify_headers(),
        json={
            "code": code,
            "description": "Live Deployment Type",
            "nominalLength": "20'",
            "maxPayloadKg": 28200,
        },
    )


def register_container(
    live_state: LiveState, equipment_type: str, ordinal: int = 1
) -> httpx.Response:
    number = f"LIVE{live_state.suffix[:6]}{ordinal}"
    live_state.latest_container_number = number
    response = request(
        live_state,
        "POST",
        "/containers",
        headers=modify_headers(),
        json={
            "containerNumber": number,
            "equipmentType": equipment_type,
            "currentDepot": live_state.depot,
        },
    )
    if response.status_code in {200, 201}:
        live_state.latest_container_id = str(response.json()["id"])
    return response


def reserve_latest_type(live_state: LiveState, booking: str) -> httpx.Response:
    live_state.latest_booking = booking
    response = request(
        live_state,
        "POST",
        "/reservations",
        headers=modify_headers(),
        json={
            "bookingReference": booking,
            "originDepot": live_state.depot,
            "equipment": [{"type": latest_equipment_type(live_state), "quantity": 1}],
        },
    )
    if response.status_code in {200, 201}:
        assigned = response.json()["assignedContainers"]
        if assigned:
            live_state.latest_container_id = str(assigned[0]["containerId"])
    return response


def container(live_state: LiveState) -> dict[str, object]:
    response = request(
        live_state,
        "GET",
        f"/containers/{latest_container_id(live_state)}",
        headers=read_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


def availability_count(live_state: LiveState) -> int:
    response = request(
        live_state,
        "GET",
        f"/availability?depotCode={live_state.depot}",
        headers=read_headers(),
    )
    assert response.status_code == 200, response.text
    equipment_type = latest_equipment_type(live_state)
    for row in response.json()["availability"]:
        if row["equipmentType"] == equipment_type:
            return int(row["availableCount"])
    return 0


@given("the live Azure deployment is reachable")
def live_deployment_is_reachable(live_state: LiveState) -> None:
    response = request(live_state, "GET", "/health")
    assert response.status_code == 200, response.text


@when(parsers.parse('I request GET "{path}" without a bearer token'))
def get_without_token(live_state: LiveState, path: str) -> None:
    request(live_state, "GET", path)


@when(parsers.parse('I request GET "{path}" with a read bearer token'))
def get_with_read_token(live_state: LiveState, path: str) -> None:
    request(live_state, "GET", path, headers=read_headers())


@when(parsers.parse('I request GET "{path}" with a bearer token for audience "{audience}"'))
def get_with_wrong_audience(live_state: LiveState, path: str, audience: str) -> None:
    request(
        live_state,
        "GET",
        path,
        headers=bearer(make_token(scopes=["equipments:read"], audience=audience)),
    )


@when("I try to register a unique live container with a read bearer token")
def try_register_with_read(live_state: LiveState) -> None:
    request(
        live_state,
        "POST",
        "/containers",
        headers=read_headers(),
        json={
            "containerNumber": f"READ{live_state.suffix[:6]}1",
            "equipmentType": "20FT",
            "currentDepot": live_state.depot,
        },
    )


@when("I register a unique live container with an admin bearer token")
def register_with_admin(live_state: LiveState) -> None:
    response = request(
        live_state,
        "POST",
        "/containers",
        headers=admin_headers(),
        json={
            "containerNumber": f"ADMN{live_state.suffix[:6]}1",
            "equipmentType": "20FT",
            "currentDepot": live_state.depot,
        },
    )
    if response.status_code in {200, 201}:
        live_state.latest_container_id = str(response.json()["id"])


@when("I create a unique live equipment type")
def create_unique_live_type(live_state: LiveState) -> None:
    create_unique_equipment_type(live_state)


@when(parsers.parse('I update the unique live equipment type description to "{description}"'))
def update_unique_type(live_state: LiveState, description: str) -> None:
    request(
        live_state,
        "PUT",
        f"/equipment-types/{latest_equipment_type(live_state)}",
        headers=modify_headers(),
        json={"description": description},
    )


@when("I register a unique live container for the unique live equipment type")
def register_unique_container(live_state: LiveState) -> None:
    register_container(live_state, latest_equipment_type(live_state))


@when(parsers.parse("I register {count:d} live containers for the unique live equipment type"))
def register_live_containers(live_state: LiveState, count: int) -> None:
    for index in range(1, count + 1):
        response = register_container(live_state, latest_equipment_type(live_state), index)
        assert response.status_code == 201, response.text


@when("I list live containers for the unique live equipment type at the unique live depot")
def list_live_containers(live_state: LiveState) -> None:
    request(
        live_state,
        "GET",
        (
            f"/containers?type={latest_equipment_type(live_state)}"
            f"&status=AVAILABLE&depot={live_state.depot}"
        ),
        headers=read_headers(),
    )


@when(parsers.parse('I manually set the latest live container status to "{status}"'))
def set_latest_container_status(live_state: LiveState, status: str) -> None:
    request(
        live_state,
        "PATCH",
        f"/containers/{latest_container_id(live_state)}/status",
        headers=modify_headers(),
        json={"status": status},
    )


@when("I reserve 1 unit of the unique live equipment type at the unique live depot")
def reserve_unique_type(live_state: LiveState) -> None:
    reserve_latest_type(live_state, f"BKG-LIVE-{live_state.suffix}-1")


@when(
    "I reserve 1 unit of the unique live equipment type at the unique live depot "
    "for a cancellation booking"
)
def reserve_cancellation(live_state: LiveState) -> None:
    reserve_latest_type(live_state, f"BKG-LIVE-{live_state.suffix}-C")


@when(
    "I reserve 1 unit of the unique live equipment type at the unique live depot "
    "for a completed booking"
)
def reserve_completed(live_state: LiveState) -> None:
    reserve_latest_type(live_state, f"BKG-LIVE-{live_state.suffix}-D")


@when("I pick up the latest live reserved container")
def pickup_latest(live_state: LiveState) -> None:
    request(
        live_state,
        "POST",
        f"/containers/{latest_container_id(live_state)}/pickup",
        headers=modify_headers(),
    )


@when("I return the latest live container")
def return_latest(live_state: LiveState) -> None:
    request(
        live_state,
        "POST",
        f"/containers/{latest_container_id(live_state)}/return",
        headers=modify_headers(),
    )


@when(parsers.parse('I receive a live "{event_type}" event for the latest live booking'))
def receive_live_event(live_state: LiveState, event_type: str) -> None:
    request(
        live_state,
        "POST",
        "/events",
        headers=modify_headers(),
        json={"eventType": event_type, "payload": {"bookingReference": latest_booking(live_state)}},
    )


@then(parsers.parse("the latest live response status is {status_code:d}"))
def latest_status(live_state: LiveState, status_code: int) -> None:
    assert live_state.response().status_code == status_code, live_state.response().text


@then(parsers.parse('the latest live JSON response has field "{field_name}" equal to "{value}"'))
def json_field_equals(live_state: LiveState, field_name: str, value: str) -> None:
    assert str(body(live_state)[field_name]) == value


@then(
    parsers.parse(
        'the latest live JSON response has boolean field "{field_name}" equal to {value}'
    )
)
def json_bool_field_equals(live_state: LiveState, field_name: str, value: str) -> None:
    assert body(live_state)[field_name] is (value == "true")


@then(parsers.parse('the latest live OpenAPI response title is "{title}"'))
def openapi_title(live_state: LiveState, title: str) -> None:
    assert body(live_state)["info"]["title"] == title


@then(parsers.parse('the latest live OpenAPI response exposes path "{path}"'))
def openapi_path(live_state: LiveState, path: str) -> None:
    assert path in body(live_state)["paths"]


@then(
    parsers.parse(
        'the latest live OpenAPI bearerAuth security scheme has type "{scheme_type}" '
        'and scheme "{scheme}"'
    )
)
def openapi_bearer_scheme(live_state: LiveState, scheme_type: str, scheme: str) -> None:
    assert body(live_state)["components"]["securitySchemes"]["bearerAuth"] == {
        "type": scheme_type,
        "scheme": scheme,
    }


@then(parsers.parse('the latest live response redirects to "{path}"'))
def redirects_to(live_state: LiveState, path: str) -> None:
    assert live_state.response().headers["location"] == path


@then(parsers.parse('the latest live error is "{message}"'))
def latest_error_is(live_state: LiveState, message: str) -> None:
    assert latest_error(live_state) == message


@then("the latest live response includes the unique live equipment type")
def response_includes_unique_type(live_state: LiveState) -> None:
    assert body(live_state)["code"] == latest_equipment_type(live_state)


@then(parsers.parse('the latest live container status is "{status}"'))
def latest_container_status(live_state: LiveState, status: str) -> None:
    payload = body(live_state)
    if "status" in payload:
        assert payload["status"] == status
        return
    assert container(live_state)["status"] == status


@then("the latest live container list includes the unique live container")
def container_list_includes_unique(live_state: LiveState) -> None:
    assert live_state.latest_container_number is not None
    containers = body(live_state)["containers"]
    assert isinstance(containers, list)
    assert any(item["containerNumber"] == live_state.latest_container_number for item in containers)


@then(
    parsers.parse(
        "live availability at the unique live depot shows {count:d} units of "
        "the unique live equipment type"
    )
)
def live_availability_shows(live_state: LiveState, count: int) -> None:
    assert availability_count(live_state) == count


@then(parsers.parse("the latest live reservation assigned {count:d} container"))
@then(parsers.parse("the latest live reservation assigned {count:d} containers"))
def reservation_assigned_count(live_state: LiveState, count: int) -> None:
    assigned = body(live_state)["assignedContainers"]
    assert isinstance(assigned, list)
    assert len(assigned) == count


@then(parsers.parse('the latest live reservation status is "{status}"'))
def reservation_status(live_state: LiveState, status: str) -> None:
    assert body(live_state)["status"] == status


@then("the latest live container booking reference is null")
def booking_reference_is_null(live_state: LiveState) -> None:
    assert container(live_state)["bookingReference"] is None
