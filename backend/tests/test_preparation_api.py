import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from gametheory.api import app
from gametheory.auth import Principal, authenticate
from gametheory.config import Settings, get_settings
from gametheory.persistence import Audit, get_db
from gametheory.preparation import BoardDraft, PriorResultReference


@pytest.fixture
def preparation_client():
    actor = Principal(str(uuid4()), str(uuid4()))
    db = MagicMock()
    app.dependency_overrides[authenticate] = lambda: actor
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            yield client, db, actor
    finally:
        app.dependency_overrides.clear()


def approval_body():
    return {
        "preview_id": str(uuid4()),
        "digest": "a" * 64,
        "decision": "approved",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "acknowledge_unverified": True,
        "note": "Static preparation only.",
    }


@pytest.mark.parametrize("administrator", [False, True])
def test_typed_me_preserves_existing_response(preparation_client, administrator):
    client, db, actor = preparation_client
    db.get.return_value = SimpleNamespace() if administrator else None
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json() == {
        "object_id": actor.object_id,
        "organization_admin": administrator,
    }
    schema = app.openapi()["paths"]["/api/me"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema == {"$ref": "#/components/schemas/MeView"}


def test_result_binding_body_reaches_service_without_literal_id_coercion(
    preparation_client, monkeypatch
):
    client, _, _ = preparation_client
    source, target = str(uuid4()), str(uuid4())
    draft = {
        "name": "Derived identifier",
        "steps": [
            {"id": source, "kind": "operation", "label": "Create"},
            {
                "id": target,
                "kind": "operation",
                "label": "Observe",
                "depends_on": [source],
                "parameters": {"record_id": {"source_step_id": source, "field": "record_id"}},
            },
        ],
    }
    save = MagicMock(side_effect=HTTPException(409, "Unit fixture reached service validation"))
    monkeypatch.setattr("gametheory.preparation_service.save_board", save)
    response = client.put(
        f"/api/workspaces/{uuid4()}/boards/{uuid4()}", json=draft, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 409
    received = save.call_args.args[4].steps[1].parameters["record_id"]
    assert isinstance(received, PriorResultReference)
    assert received.model_dump(mode="json") == {"source_step_id": source, "field": "record_id"}


def test_api_rejects_result_from_mutually_exclusive_branch_before_saving(
    preparation_client, monkeypatch
):
    client, db, _ = preparation_client
    root, condition, source, target = [str(uuid4()) for _ in range(4)]
    draft = {
        "name": "Invalid sibling reference",
        "steps": [
            {"id": root, "kind": "operation", "label": "Observe"},
            {
                "id": condition,
                "kind": "condition",
                "label": "Compare",
                "depends_on": [root],
                "condition": {
                    "source_step_id": root,
                    "result_field": "ready",
                    "operator": "eq",
                    "value": True,
                    "if_true": [source],
                    "if_false": [target],
                },
            },
            {"id": source, "kind": "operation", "label": "True-branch source"},
            {
                "id": target,
                "kind": "operation",
                "label": "False-branch target",
                "depends_on": [source],
                "parameters": {"record_id": {"source_step_id": source, "field": "record_id"}},
            },
        ],
    }
    save = MagicMock()
    monkeypatch.setattr("gametheory.preparation_service.save_board", save)
    response = client.put(
        f"/api/workspaces/{uuid4()}/boards/{uuid4()}", json=draft, headers={"If-Match": '"1"'}
    )
    assert response.status_code == 422
    save.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "method,suffix,body",
    [
        ("put", "", BoardDraft(name="Review").model_dump(mode="json")),
        ("post", "/previews", None),
        ("post", "/approvals", approval_body()),
        ("post", f"/approvals/{uuid4()}/revoke", None),
        ("post", "/execute", None),
    ],
)
@pytest.mark.parametrize(
    "match,status", [(None, 428), ('W/"1"', 400), ("1", 400), ('"1","2"', 400), ('"0"', 400)]
)
def test_conditional_preparation_endpoints_require_strong_numeric_etag(
    preparation_client, method, suffix, body, match, status
):
    client, db, _ = preparation_client
    response = getattr(client, method)(
        f"/api/workspaces/{uuid4()}/boards/{uuid4()}{suffix}",
        json=body,
        headers={"If-Match": match} if match else {},
    )
    assert response.status_code == status, response.text
    db.execute.assert_not_called()
    db.add.assert_not_called()
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"


def test_configuration_withdrawal_has_its_own_version_precondition(preparation_client):
    client, db, _ = preparation_client
    response = client.post(
        f"/api/workspaces/{uuid4()}/connections/{uuid4()}/configurations/{uuid4()}/withdraw"
    )
    assert response.status_code == 428
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "method,path,body,service_name",
    [
        (
            "post",
            "/boards",
            {"name": "Board", "scenario_id": str(uuid4()), "revision_version": 1},
            "create_board",
        ),
        (
            "post",
            f"/connections/{uuid4()}/configurations",
            {"catalog": {"name": "Unconfigured", "operations": []}},
            "register_configuration",
        ),
        ("put", "/approvers", {"object_id": str(uuid4())}, "grant_approver"),
        ("delete", f"/approvers/{uuid4()}", None, "revoke_approver"),
    ],
)
def test_append_and_grant_endpoints_do_not_invent_collection_preconditions(
    preparation_client, monkeypatch, method, path, body, service_name
):
    client, db, _ = preparation_client
    mutation = MagicMock(side_effect=HTTPException(409, "Unit fixture reached authorized service"))
    monkeypatch.setattr(f"gametheory.preparation_service.{service_name}", mutation)
    kwargs = {}
    if method != "delete":
        kwargs["json"] = body
    response = getattr(client, method)(f"/api/workspaces/{uuid4()}" + path, **kwargs)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "Unit fixture reached authorized service"
    mutation.assert_called_once()
    db.execute.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "suffix,service_name",
    [
        ("/boards", "list_boards"),
        ("/approvers", "list_approvers"),
        (f"/connections/{uuid4()}/configurations", "list_configurations"),
    ],
)
def test_collection_reads_return_plain_arrays_without_version_headers(
    preparation_client, monkeypatch, suffix, service_name
):
    rows = MagicMock(return_value=[])
    monkeypatch.setattr(f"gametheory.preparation_service.{service_name}", rows)
    response = preparation_client[0].get(f"/api/workspaces/{uuid4()}" + suffix)
    assert response.status_code == 200
    assert response.json() == []
    assert "etag" not in response.headers
    assert "x-collection-etag" not in response.headers
    rows.assert_called_once()


@pytest.mark.parametrize(
    "payload",
    [
        '{"name":"First","name":"Second","scenario_id":"00000000-0000-0000-0000-000000000001","revision_version":1}',
        '{"name":"Example","scenario_id":"00000000-0000-0000-0000-000000000001","revision_version":NaN}',
        '{"name":"Example","scenario_id":"00000000-0000-0000-0000-000000000001","revision_version":1e999}',
    ],
)
def test_json_duplicate_and_nonfinite_values_fail_before_mutation(
    preparation_client, monkeypatch, payload
):
    client, db, _ = preparation_client
    create = MagicMock()
    monkeypatch.setattr("gametheory.preparation_service.create_board", create)
    response = client.post(
        f"/api/workspaces/{uuid4()}/boards",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    create.assert_not_called()
    db.add.assert_not_called()


def test_approval_contract_rejects_unacknowledged_naive_or_extra_authority(
    preparation_client, monkeypatch
):
    client, db, _ = preparation_client
    decide = MagicMock()
    monkeypatch.setattr("gametheory.preparation_service.decide_preparation", decide)
    for changed in (
        {"acknowledge_unverified": False},
        {"acknowledge_unverified": 1},
        {"expires_at": "2026-12-01T00:00:00"},
        {"execution_authorized": True},
        {"kind": "execution"},
        {"digest": "caller-chosen-not-a-digest"},
    ):
        response = client.post(
            f"/api/workspaces/{uuid4()}/boards/{uuid4()}/approvals",
            json=approval_body() | changed,
            headers={"If-Match": '"1"'},
        )
        assert response.status_code == 422, response.text
    decide.assert_not_called()
    db.add.assert_not_called()


def test_execution_gate_is_permanent_and_never_creates_a_dispatch(preparation_client, monkeypatch):
    client, db, _ = preparation_client
    board = SimpleNamespace(id=str(uuid4()), version=7)
    gate = MagicMock(return_value=board)
    monkeypatch.setattr("gametheory.preparation_service.board_record", gate)
    path = f"/api/workspaces/{uuid4()}/boards/{board.id}/execute"
    for body in (None, {"kind": "execution", "execution_authorized": True, "approved": True}):
        response = client.post(path, json=body, headers={"If-Match": '"7"'})
        assert response.status_code == 501
        assert response.json()["code"] == "execution_disabled"
        assert response.json()["execution_authorized"] is False
        assert "not authorization" in response.json()["message"]
    assert client.get("/api/config").json()["capabilities"]["execution"] is False
    db.execute.assert_not_called()
    events = [call.args[0] for call in db.add.call_args_list]
    assert all(
        isinstance(event, Audit) and event.operation == "execution.disabled" for event in events
    )
    assert len(events) == 2
    assert all(call.args[-1] == "editor" for call in gate.call_args_list)


def test_execution_request_still_requires_workspace_authorization(preparation_client, monkeypatch):
    client, db, _ = preparation_client
    monkeypatch.setattr(
        "gametheory.preparation_service.board_record",
        MagicMock(side_effect=HTTPException(404, "Board not found")),
    )
    assert (
        client.post(
            f"/api/workspaces/{uuid4()}/boards/{uuid4()}/execute",
            headers={"If-Match": '"1"'},
        ).status_code
        == 404
    )
    db.add.assert_not_called()


def test_new_routes_authenticate_before_opening_sql(monkeypatch):
    settings = Settings(_env_file=None)
    app.dependency_overrides[get_settings] = lambda: settings
    db = MagicMock(side_effect=AssertionError("SQL must not be opened before authentication"))
    app.dependency_overrides[get_db] = db
    try:
        with TestClient(app) as client:
            for suffix in ("boards", "approvers", f"connections/{uuid4()}/configurations"):
                response = client.get(f"/api/workspaces/{uuid4()}/{suffix}")
                assert response.status_code == 503
                assert "Entra authentication" in response.json()["detail"]
            db.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_openapi_exposes_frozen_contracts_before_spa_catchall(preparation_client, monkeypatch):
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/workspaces/{wid}/boards/{bid}" in paths
    assert "/api/workspaces/{wid}/connections/{cid}/configurations" in paths
    assert "BoardDraft" in schema["components"]["schemas"]
    assert "PreparationApprovalInput" in schema["components"]["schemas"]
    assert (
        schema["components"]["schemas"]["ExecutionDisabled"]["properties"]["execution_authorized"][
            "const"
        ]
        is False
    )
    monkeypatch.setattr("gametheory.preparation_service.list_boards", lambda *_: [])
    response = preparation_client[0].get(f"/api/workspaces/{uuid4()}/boards")
    assert response.status_code == 200
    assert response.json() == []
    assert "exercise-execution/v1" not in json.dumps(paths)
