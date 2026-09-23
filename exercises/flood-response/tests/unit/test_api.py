from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from flood_lab.api import create_app, service
from flood_lab.auth import Actor, current_actor
from flood_lab.config import Settings
from flood_lab.contracts import EventList, MilestonesView, RequestList, RunList, version_value
from flood_lab.service import OperationResponse

RUN, RECORD, VERSION = uuid4(), uuid4(), version_value(uuid4())
ETAG = f'"{VERSION}"'
WRITE_HEADERS = {"Idempotency-Key": "TEST-ONLY", "If-Match": ETAG}


class TestOnlyService:
    """Pure transport fixture. Does not emulate SQL or establish integration evidence."""

    __test__ = False

    def __init__(self):
        self.milestone_calls = []

    def list_runs(self, *args):
        return RunList(items=[], next_offset=None)

    def requests(self, *args):
        return RequestList(items=[], next_after=None)

    def events(self, *args):
        return EventList(items=[], next_after=None)

    def milestones(self, actor, run_id, request_id, acknowledge_within, allocate_within):
        now = datetime(2030, 1, 1, tzinfo=UTC)
        self.milestone_calls.append((run_id, request_id, acknowledge_within, allocate_within))
        return MilestonesView(
            request_id=request_id,
            run_id=run_id,
            record_version=VERSION,
            status="open",
            quantity_requested=4,
            quantity_allocated=0,
            created_at=now,
            created_event_id=None,
            acknowledged=False,
            acknowledged_at=None,
            acknowledgement_event_id=None,
            allocated_total_by_deadline=0,
            allocation_completed_at=None,
            allocation_completed_event_id=None,
            allocation_event_count=0,
            acknowledgement_deadline=now,
            allocation_deadline=now,
            as_of=now,
            acknowledged_on_time=None,
            allocated_on_time=None,
            acknowledgement_reason="TEST ONLY transport fixture",
            allocation_reason="TEST ONLY transport fixture",
        )

    def acknowledge(self, actor, run_id, request_id, body, correlation_id):
        return OperationResponse(
            200,
            {
                "outcome": "succeeded",
                "correlation_id": str(correlation_id),
                "record_version": VERSION,
            },
        )


@pytest.fixture
def client():
    app = create_app(
        Settings(
            entra_tenant_id=str(uuid4()),
            entra_audience="api://test-only",
            database_url="",
            database_name="",
            allowed_origins=[],
        )
    )
    # These explicit overrides exist only in tests; the production bundle has no switch.
    app.dependency_overrides[current_actor] = lambda: Actor(uuid4(), uuid4(), "user")
    app.dependency_overrides[service] = lambda: TestOnlyService()
    return TestClient(app, raise_server_exceptions=False)


def test_missing_auth_configuration_is_setup_required_not_bypass():
    app = create_app(Settings(entra_tenant_id="", entra_audience="", allowed_origins=[]))
    response = TestClient(app).get("/v1/runs")
    assert response.status_code == 503
    assert response.json()["code"] == "setup_required"
    assert response.json()["outcome"] == "failed"


def test_configured_auth_requires_bearer_token():
    app = create_app(
        Settings(
            entra_tenant_id=str(uuid4()),
            entra_audience="api://test-only",
            allowed_origins=[],
        )
    )
    response = TestClient(app).get("/v1/runs")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_bounded_read_endpoints(client):
    assert client.get("/v1/runs").status_code == 200
    assert client.get(f"/v1/runs/{RUN}/requests?limit=100").status_code == 200
    for query in ("limit=101", "limit=0", "after=-1", "status=approved"):
        response = client.get(f"/v1/runs/{RUN}/requests?{query}")
        assert response.status_code == 422
    assert client.get(f"/v1/runs/{RUN}/events?limit=101").status_code == 422


def test_milestone_windows_are_required_bounded_integer_query_fields(client):
    fixture = TestOnlyService()
    client.app.dependency_overrides[service] = lambda: fixture
    path = f"/v1/runs/{RUN}/requests/{RECORD}/milestones"
    response = client.get(
        path, params={"acknowledge_within_seconds": 1, "allocate_within_seconds": 604800}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["contract_version"] == "flood-lab-milestones/v1"
    assert response.json()["acknowledged_on_time"] is None
    assert fixture.milestone_calls == [(RUN, RECORD, 1, 604800)]
    for query in (
        {},
        {"acknowledge_within_seconds": 600},
        {"allocate_within_seconds": 1200},
        {"acknowledge_within_seconds": 0, "allocate_within_seconds": 1200},
        {"acknowledge_within_seconds": 600, "allocate_within_seconds": 604801},
        {"acknowledge_within_seconds": "1.5", "allocate_within_seconds": 1200},
        {"acknowledge_within_seconds": "true", "allocate_within_seconds": 1200},
        {"acknowledge_within_seconds": "", "allocate_within_seconds": 1200},
    ):
        response = client.get(path, params=query)
        assert response.status_code == 422, query
        assert response.json()["code"] == "invalid_request"
    invalid_record = f"/v1/runs/{RUN}/requests/not-a-uuid/milestones"
    valid_windows = {"acknowledge_within_seconds": 600, "allocate_within_seconds": 1200}
    assert client.get(invalid_record, params=valid_windows).status_code == 422
    assert len(fixture.milestone_calls) == 1


def test_if_match_is_strong_and_protocol_parameters_are_not_json(client):
    path = f"/v1/runs/{RUN}/requests/{RECORD}/acknowledge"
    assert client.post(path, json={}, headers={"Idempotency-Key": "TEST-ONLY"}).status_code == 428
    for invalid in ("*", VERSION, f"W/{ETAG}", f"{ETAG},{ETAG}"):
        assert (
            client.post(path, json={}, headers=WRITE_HEADERS | {"If-Match": invalid}).status_code
            == 422
        )
    for reserved in ({"expected_version": VERSION}, {"idempotency_key": "JSON-IS-FORBIDDEN"}):
        assert client.post(path, json=reserved, headers=WRITE_HEADERS).status_code == 422
    response = client.post(path, json={}, headers=WRITE_HEADERS)
    assert response.status_code == 200
    assert response.headers["etag"] == ETAG
    assert response.json()["record_version"] == VERSION


def test_validation_never_echoes_input_and_bodies_are_bounded(client):
    path = f"/v1/runs/{RUN}/requests"
    marker = "DO-NOT-ECHO-PRIVATE-INPUT"
    response = client.post(path, json={"bad_field": marker})
    assert response.status_code == 422
    assert marker not in response.text
    assert "correlation_id" in response.json()
    response = client.post(path, content="x" * 17000)
    assert response.status_code == 413


def test_database_failure_is_unknown_for_writes_without_raw_error_logging(client, caplog):
    class Broken(TestOnlyService):
        def acknowledge(self, *args):
            raise OperationalError("DO-NOT-LOG-SQL-OR-TOKENS", {}, Exception("sensitive body"))

    client.app.dependency_overrides[service] = lambda: Broken()
    response = client.post(
        f"/v1/runs/{RUN}/requests/{RECORD}/acknowledge",
        json={},
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 503
    assert response.json()["outcome"] == "unknown"
    assert "DO-NOT-LOG" not in response.text + caplog.text
    assert "sensitive body" not in response.text + caplog.text
    assert response.headers["x-correlation-id"] == response.json()["correlation_id"]


def test_no_participant_seed_reset_approval_or_graph_routes(client):
    paths = set(client.app.openapi()["paths"])
    assert not any(word in path for path in paths for word in ("seed", "reset", "approve", "send"))
    assert client.get("/health").json()["graph_sending"] is False


def test_api_rejects_non_utc_allocation_input(client):
    response = client.post(
        f"/v1/runs/{RUN}/requests/{RECORD}/allocate",
        json={
            "quantity": 1,
            "available_at": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        },
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 422


def test_unexpected_errors_do_not_escape_to_raw_asgi_logging(client, caplog):
    class Broken(TestOnlyService):
        def acknowledge(self, *args):
            raise RuntimeError("DO-NOT-LOG-RAW-TOKEN-OR-BODY")

    client.app.dependency_overrides[service] = lambda: Broken()
    response = client.post(
        f"/v1/runs/{RUN}/requests/{RECORD}/acknowledge",
        json={},
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 500
    assert response.json()["outcome"] == "unknown"
    assert "DO-NOT-LOG" not in response.text + caplog.text


def test_idempotency_header_is_required_and_bounded(client):
    path = f"/v1/runs/{RUN}/requests/{RECORD}/acknowledge"
    assert client.post(path, json={}, headers={"If-Match": ETAG}).status_code == 422
    for invalid in ("", "has spaces", "x" * 129):
        assert (
            client.post(
                path, json={}, headers=WRITE_HEADERS | {"Idempotency-Key": invalid}
            ).status_code
            == 422
        )


def test_headers_build_internal_preconditions_without_body_control_fields(client):
    commands = []

    class Capture(TestOnlyService):
        def acknowledge(self, actor, run_id, request_id, body, correlation_id):
            commands.append(body)
            return super().acknowledge(actor, run_id, request_id, body, correlation_id)

        def allocate(self, actor, run_id, request_id, body, correlation_id):
            commands.append(body)
            return super().acknowledge(actor, run_id, request_id, body, correlation_id)

        def create_request(self, actor, run_id, body, correlation_id):
            commands.append(body)
            return OperationResponse(
                201, {"correlation_id": str(correlation_id), "record_version": VERSION}
            )

    client.app.dependency_overrides[service] = lambda: Capture()
    assert (
        client.post(
            f"/v1/runs/{RUN}/requests/{RECORD}/acknowledge", json={}, headers=WRITE_HEADERS
        ).status_code
        == 200
    )
    assert commands[-1].expected_version == VERSION
    assert commands[-1].idempotency_key == "TEST-ONLY"
    assert (
        client.post(
            f"/v1/runs/{RUN}/requests/{RECORD}/allocate",
            json={"quantity": 4, "available_at": "2030-01-01T10:05:00Z"},
            headers=WRITE_HEADERS,
        ).status_code
        == 200
    )
    assert commands[-1].quantity == 4
    assert commands[-1].expected_version == VERSION
    assert (
        client.post(
            f"/v1/runs/{RUN}/requests",
            json={
                "shelter_id": str(uuid4()),
                "resource_type": "cots",
                "quantity_requested": 4,
                "summary": "TEST ONLY",
                "needed_by": "2030-01-01T10:20:00Z",
            },
            headers={"Idempotency-Key": "TEST-ONLY-CREATE"},
        ).status_code
        == 201
    )
    assert commands[-1].idempotency_key == "TEST-ONLY-CREATE"


def test_preflight_allows_only_the_fixed_write_control_headers():
    origin = "http://127.0.0.1:5173"
    app = create_app(Settings(allowed_origins=[origin]))
    response = TestClient(app).options(
        f"/v1/runs/{RUN}/requests/{RECORD}/allocate",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization,Content-Type,If-Match,Idempotency-Key",
        },
    )
    assert response.status_code == 200
    assert "idempotency-key" in response.headers["access-control-allow-headers"].lower()
