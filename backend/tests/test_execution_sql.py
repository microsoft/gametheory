import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from execution_support import execution_case
from sqlalchemy import select
from test_preparation_sql import require

from gametheory import execution_worker as worker
from gametheory.api import app
from gametheory.auth import Principal, authenticate
from gametheory.execution_adapters import AdapterResult
from gametheory.persistence import (
    ExerciseRun,
    ExerciseRunState,
    RunDispatch,
    RunStep,
    now,
)

pytestmark = pytest.mark.integration


def dispatch_for(factory, rid):
    with factory() as db:
        return db.scalar(select(RunDispatch.id).where(RunDispatch.run_id == rid))


def test_nonproduction_runs_without_fabricating_approval_and_persists_effect_evidence(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = execution_case(sql_client, sql_factory, monkeypatch, tmp_path)
    authorized = require(case.control("authorize"))
    assert authorized["approval_status"] == "not_required"
    started = require(case.control("start"))
    assert started["state"] == "queued"
    calls = []
    monkeypatch.setattr(
        worker,
        "invoke",
        lambda *args: (
            calls.append(args) or AdapterResult(outcome="succeeded", values={"quantity": 86})
        ),
    )
    did = dispatch_for(sql_factory, case.run["id"])
    assert worker.advance(did)["done"] is False
    assert worker.advance(did)["done"] is True
    result = require(case.client.get(case.run_path))
    assert result["state"] == "completed"
    assert result["approval_status"] == "not_required"
    assert result["steps"][0]["result"]["quantity"] == 86
    assert len(calls) == 1
    assert worker.advance(did)["done"] is True
    assert len(calls) == 1
    assert any(item["kind"] == "operation.succeeded" for item in result["events"])


def test_production_needs_independent_explicit_approval_and_revocation_blocks_dispatch(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = execution_case(sql_client, sql_factory, monkeypatch, tmp_path, "production")
    authorized = require(case.control("authorize"))
    assert authorized["approval_required"] is True
    assert case.control("start").status_code == 409
    body = {
        "context_id": authorized["context_id"],
        "manifest_digest": authorized["manifest_digest"],
        "decision": "approved",
        "expires_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        "note": "Synthetic SQL authorization fixture; no production target exists.",
    }
    assert (
        case.client.post(
            case.run_path + "/approvals",
            json=body,
            headers={"If-Match": f'"{authorized["version"]}"'},
        ).status_code
        == 403
    )
    reviewer = Principal(case.actor.tenant, str(uuid4()))
    require(
        case.client.put(
            case.workspace + "/members", json={"object_id": reviewer.object_id, "role": "viewer"}
        )
    )
    require(
        case.client.put(
            case.workspace + "/execution-grants",
            json={"object_id": reviewer.object_id, "capability": "reviewer"},
        )
    )
    app.dependency_overrides[authenticate] = lambda: reviewer
    reviewed = require(
        case.client.post(
            case.run_path + "/approvals",
            json=body,
            headers={"If-Match": f'"{authorized["version"]}"'},
        )
    )
    assert reviewed["approval_status"] == "approved"
    app.dependency_overrides[authenticate] = lambda: case.actor
    require(case.control("start"))
    approval_id = next(
        item["detail"]["approval_id"]
        for item in reviewed["events"]
        if item["kind"] == "run.reviewed"
    )
    current = require(case.client.get(case.run_path))
    require(
        case.client.post(
            case.run_path + f"/approvals/{approval_id}/revoke",
            headers={"If-Match": f'"{current["version"]}"'},
        )
    )
    calls = []
    monkeypatch.setattr(worker, "invoke", lambda *_: calls.append(True))
    worker.advance_v1(None, dispatch_for(sql_factory, case.run["id"]))
    assert calls == []
    assert require(case.client.get(case.run_path))["state"] == "intervention"


def test_new_environment_policy_holds_queued_run_until_explicit_reauthorization(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = execution_case(sql_client, sql_factory, monkeypatch, tmp_path)
    require(case.control("authorize"))
    require(case.control("start"))
    require(
        case.client.put(
            case.policy_path,
            headers={"If-Match": '"2"'},
            json={
                "classification": "nonproduction",
                "execution_enabled": True,
                "approval_required": True,
            },
        )
    )
    calls = []
    monkeypatch.setattr(worker, "invoke", lambda *_: calls.append(True))
    worker.advance_v1(None, dispatch_for(sql_factory, case.run["id"]))
    held = require(case.client.get(case.run_path))
    assert held["state"] == "intervention" and held["approval_required"]
    assert not calls
    require(
        case.client.put(
            case.policy_path,
            headers={"If-Match": '"3"'},
            json={
                "classification": "nonproduction",
                "execution_enabled": True,
                "approval_required": False,
            },
        )
    )
    assert case.control("resume").status_code == 409
    require(case.control("authorize"))
    require(case.control("resume"))
    assert not calls


def test_expired_attempt_is_unknown_not_automatically_replayed(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = execution_case(sql_client, sql_factory, monkeypatch, tmp_path)
    require(case.control("authorize"))
    require(case.control("start"))
    with sql_factory.begin() as db:
        step = db.scalar(select(RunStep).where(RunStep.run_id == case.run["id"]))
        step.state, step.attempt_id, step.lease_until = (
            "in_flight",
            str(uuid4()),
            now() - timedelta(seconds=1),
        )
    calls = []
    monkeypatch.setattr(worker, "invoke", lambda *_: calls.append(True))
    worker.advance_v1(None, dispatch_for(sql_factory, case.run["id"]))
    current = require(case.client.get(case.run_path))
    assert current["state"] == "intervention"
    assert current["steps"][0]["state"] == "unknown"
    assert not calls
    require(case.control("stop"))
    worker.advance_v1(None, dispatch_for(sql_factory, case.run["id"]))
    assert require(case.client.get(case.run_path))["state"] == "stopped_incomplete"
    assert case.control("resume").status_code == 409


def test_pause_and_stop_cannot_dispatch_pending_steps(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = execution_case(sql_client, sql_factory, monkeypatch, tmp_path)
    require(case.control("authorize"))
    require(case.control("start"))
    require(case.control("pause"))
    calls = []
    monkeypatch.setattr(worker, "invoke", lambda *_: calls.append(True))
    did = dispatch_for(sql_factory, case.run["id"])
    assert worker.advance(did)["done"] is False
    assert not calls
    require(case.control("stop"))
    assert worker.advance(did)["done"] is True
    result = require(case.client.get(case.run_path))
    assert result["state"] == "stopped"
    assert not calls


def test_run_input_pins_survive_later_board_edits_and_run_manifest_is_not_mutable_state(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = execution_case(sql_client, sql_factory, monkeypatch, tmp_path)
    original = case.run["manifest_digest"]
    board = require(case.client.get(case.board_path))
    require(
        case.client.put(
            case.board_path,
            headers={"If-Match": f'"{board["version"]}"'},
            json={**board["draft"], "name": "Later preparation"},
        )
    )
    current = require(case.client.get(case.run_path))
    assert current["manifest_digest"] == original
    assert current["manifest"]["preparation"]["draft"]["name"] != "Later preparation"
    with sql_factory() as db:
        assert db.get(ExerciseRun, case.run["id"]).digest == original
        assert db.get(ExerciseRunState, case.run["id"]).state == "prepared"


def test_invalid_readiness_ids_and_stale_run_actions_are_rejected(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = execution_case(sql_client, sql_factory, monkeypatch, tmp_path)
    assert (
        case.client.post(
            case.run_path + "/controls", json={"action": "start", "note": "fixture"}
        ).status_code
        == 428
    )
    require(case.control("authorize"))
    assert (
        case.client.post(
            case.run_path + "/controls",
            headers={"If-Match": '"1"'},
            json={"action": "start", "note": "fixture"},
        ).status_code
        == 409
    )
    with sql_factory.begin() as db:
        from gametheory.persistence import RunAuthorization

        state = db.get(ExerciseRunState, case.run["id"])
        context = db.get(RunAuthorization, state.context_id)
        # Only the privileged test fixture can alter immutable authorization data.
        context.readiness_ids = json.dumps([str(uuid4())])
    assert case.control("start").status_code == 409


def test_run_attempt_events_are_append_only_in_runtime_grants():
    from gametheory.database_setup import API_PERMISSIONS, EXECUTOR_PERMISSIONS, WORKER_PERMISSIONS

    assert EXECUTOR_PERMISSIONS["run_events"] == "SELECT, INSERT"
    assert EXECUTOR_PERMISSIONS["exercise_runs"] == "SELECT"
    assert API_PERMISSIONS["run_authorizations"] == "SELECT, INSERT"
    assert "execution_grants" not in WORKER_PERMISSIONS
    assert "target_readiness" not in WORKER_PERMISSIONS
