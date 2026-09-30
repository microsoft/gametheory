"""Run-check suggestions against real SQL Server; skipped without GT_TEST_SQL_URL.

The model is an explicit test fixture. The worker activity runs in-process against the
same disposable database; Scheduler dispatch is covered by test_scheduler_integration.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from execution_support import execution_case
from sqlalchemy import func, select
from test_preparation_sql import preview, require

from gametheory import run_setup_api
from gametheory import worker as planning_worker
from gametheory.api import app
from gametheory.auth import Principal, authenticate
from gametheory.persistence import Audit, ExerciseRun, RunSetupDispatchIntent, RunSetupRequest
from gametheory.run_setup import RunCheckSuggestion

pytestmark = pytest.mark.integration


def assistant(case, monkeypatch, sql_factory, *, enabled=True):
    monkeypatch.setattr(
        run_setup_api, "get_settings", lambda: SimpleNamespace(run_assistant_enabled=enabled)
    )
    monkeypatch.setattr(planning_worker, "session_factory", lambda: sql_factory)
    case.suggestions = case.board_path + "/run-setup/suggestions"
    return case


def ask(case, prompt="Watch the record until its quantity reaches 10.", frozen=None):
    frozen = frozen or case.preview
    body = {
        "preview_id": frozen["id"],
        "preview_digest": frozen["digest"],
        "prompt": prompt,
        "request_id": str(uuid4()),
    }
    return case.client.post(case.suggestions, json=body), body


def fixture_model(monkeypatch, suggestion):
    async def generate(context, prompt, history):
        return RunCheckSuggestion.model_validate(suggestion)

    monkeypatch.setattr(planning_worker, "generate_run_checks", generate)


def audits(sql_factory, operation, resource_id):
    with sql_factory() as db:
        return list(
            db.scalars(
                select(Audit).where(Audit.operation == operation, Audit.resource_id == resource_id)
            )
        )


def observation(case, **changes):
    return {
        "step_id": case.draft["steps"][0]["id"],
        "field": "quantity",
        "operator": "gte",
        "value": 10,
        "interval_seconds": 5,
        "timeout_seconds": 60,
        "max_samples": 12,
    } | changes


def create(case, body, frozen=None, version=None):
    frozen = frozen or case.preview
    return case.client.post(
        case.board_path + "/runs",
        headers={"If-Match": f'"{version or case.board_version}"'},
        json={"preview_id": frozen["id"], "preview_digest": frozen["digest"]} | body,
    )


def test_operator_suggestion_lifecycle_links_provenance_outside_the_manifest(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = assistant(
        execution_case(sql_client, sql_factory, monkeypatch, tmp_path), monkeypatch, sql_factory
    )
    response, body = ask(case)
    rid = body["request_id"]
    assert response.status_code == 202, response.text
    assert response.json() == {"id": rid, "status": "queued"}
    assert require(case.client.post(case.suggestions, json=body), 202)["id"] == rid
    assert case.client.post(case.suggestions, json=body | {"prompt": "Other"}).status_code == 409
    assert ask(case, "Suggest checks for every goal.")[0].status_code == 409
    [queued] = require(case.client.get(case.suggestions))
    assert (queued["id"], queued["status"], queued["is_current"]) == (rid, "queued", True)
    assert queued["summary"] is None and queued["observations"] == []
    assert len(audits(sql_factory, "run_setup.suggestion_requested", rid)) == 1
    with sql_factory() as db:
        stored = db.get(RunSetupRequest, rid)
        assert db.get(RunSetupDispatchIntent, rid).state == "pending"
        content = case.config["content"]
        for value in (content["endpoint"], content["resource_id"], content["identity_ref"]):
            assert value not in stored.context
        assert json.loads(stored.context)["steps"][0]["operation"]["effect"] == "read"

    fixture_model(
        monkeypatch,
        {
            "summary": "Watch the record quantity until it reaches 10.",
            "observations": [observation(case), observation(case, step_id=str(uuid4()))],
            "questions": ["Which recorded result shows the goal was met?"],
        },
    )
    assert planning_worker.create_run_check_suggestion(rid) == rid
    planning_worker.create_run_check_suggestion(rid)
    assert len(audits(sql_factory, "run_setup.suggested", rid)) == 1
    [proposed] = require(case.client.get(case.suggestions))
    assert proposed["status"] == "proposed" and proposed["is_current"] is True
    assert proposed["summary"] == "Watch the record quantity until it reaches 10."
    assert proposed["questions"] == ["Which recorded result shows the goal was met?"]
    assert [item["valid"] for item in proposed["observations"]] == [True, False]
    assert proposed["observations"][1]["issues"] == [
        "Only registered read operations may be polled"
    ]

    link = {"observations": [observation(case)], "suggestion_id": rid}
    checked = require(
        case.client.post(
            case.board_path + "/runs/preflight",
            headers={"If-Match": f'"{case.board_version}"'},
            json={"preview_id": case.preview["id"], "preview_digest": case.preview["digest"]}
            | link,
        )
    )
    assert checked["valid"] is True
    assert create(case, link | {"suggestion_id": str(uuid4())}).status_code == 409
    run = require(create(case, link), 201)
    prepared = next(item for item in run["events"] if item["kind"] == "run.prepared")
    assert prepared["detail"]["suggestion_id"] == rid
    assert "suggestion_id" not in json.dumps(run["manifest"])
    with sql_factory() as db:
        assert "suggestion_id" not in db.get(ExerciseRun, run["id"]).manifest
    [used] = audits(sql_factory, "run_setup.suggestion_used", rid)
    [created] = audits(sql_factory, "execution.prepared", run["id"])
    assert used.correlation_id == created.correlation_id
    # The run without a link keeps its original event shape.
    original = next(item for item in case.run["events"] if item["kind"] == "run.prepared")
    assert "suggestion_id" not in original["detail"]


def test_stale_previews_and_suggestions_for_other_previews_are_refused(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = assistant(
        execution_case(sql_client, sql_factory, monkeypatch, tmp_path), monkeypatch, sql_factory
    )
    _, body = ask(case)
    fixture_model(
        monkeypatch, {"summary": "Watch the record.", "observations": [observation(case)]}
    )
    planning_worker.create_run_check_suggestion(body["request_id"])
    board = require(case.client.get(case.board_path))
    saved = require(
        case.client.put(
            case.board_path,
            headers={"If-Match": f'"{board["version"]}"'},
            json={**board["draft"], "name": "Edited after the suggestion"},
        )
    )
    [stale] = require(case.client.get(case.suggestions))
    assert (stale["status"], stale["is_current"]) == ("proposed", False)
    assert stale["observations"][0]["valid"] is True
    assert ask(case)[0].status_code == 409
    frozen = preview(case.client, case.board_path, saved["version"])
    link = {"observations": [observation(case)], "suggestion_id": body["request_id"]}
    assert create(case, link, frozen, saved["version"]).status_code == 409
    assert (
        case.client.post(
            case.board_path + "/runs/preflight",
            headers={"If-Match": f'"{saved["version"]}"'},
            json={"preview_id": frozen["id"], "preview_digest": frozen["digest"]} | link,
        ).status_code
        == 409
    )
    fresh, fresh_body = ask(case, frozen=frozen)
    assert fresh.status_code == 202
    planning_worker.fail_run_check(fresh_body["request_id"], "Explicit test cleanup.")
    assert [item["is_current"] for item in require(case.client.get(case.suggestions))] == [
        True,
        False,
    ]


def test_only_explicit_operators_can_request_or_read_suggestions(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = assistant(
        execution_case(sql_client, sql_factory, monkeypatch, tmp_path), monkeypatch, sql_factory
    )
    editor = Principal(case.actor.tenant, str(uuid4()))
    require(
        case.client.put(
            case.workspace + "/members", json={"object_id": editor.object_id, "role": "editor"}
        )
    )
    outsider = Principal(case.actor.tenant, str(uuid4()))
    try:
        app.dependency_overrides[authenticate] = lambda: editor
        assert case.client.get(case.suggestions).status_code == 403
        response, body = ask(case)
        assert response.status_code == 403
        app.dependency_overrides[authenticate] = lambda: outsider
        assert case.client.get(case.suggestions).status_code == 404
        assert ask(case)[0].status_code == 404
    finally:
        app.dependency_overrides[authenticate] = lambda: case.actor
    with sql_factory() as db:
        assert db.get(RunSetupRequest, body["request_id"]) is None
        count = select(func.count()).select_from(RunSetupRequest)
        assert db.scalar(count.where(RunSetupRequest.board_id == case.board["id"])) == 0


def test_disabled_assistant_returns_503_and_records_nothing(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = assistant(
        execution_case(sql_client, sql_factory, monkeypatch, tmp_path),
        monkeypatch,
        sql_factory,
        enabled=False,
    )
    response, body = ask(case)
    assert response.status_code == 503
    assert "forms remain available" in response.json()["detail"]
    assert case.client.get(case.suggestions).status_code == 503
    with sql_factory() as db:
        assert db.get(RunSetupRequest, body["request_id"]) is None
    assert audits(sql_factory, "run_setup.suggestion_requested", body["request_id"]) == []


def test_worker_rechecks_membership_before_calling_the_model(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    case = assistant(
        execution_case(sql_client, sql_factory, monkeypatch, tmp_path), monkeypatch, sql_factory
    )
    operator = Principal(case.actor.tenant, str(uuid4()))
    require(
        case.client.put(
            case.workspace + "/members", json={"object_id": operator.object_id, "role": "viewer"}
        )
    )
    require(
        case.client.put(
            case.workspace + "/execution-grants",
            json={"object_id": operator.object_id, "capability": "operator"},
        )
    )
    try:
        app.dependency_overrides[authenticate] = lambda: operator
        response, body = ask(case)
        assert response.status_code == 202, response.text
    finally:
        app.dependency_overrides[authenticate] = lambda: case.actor
    assert case.client.delete(case.workspace + f"/members/{operator.object_id}").status_code == 204
    calls = []

    async def generate(context, prompt, history):
        calls.append(prompt)
        return RunCheckSuggestion(summary="Should never be published")

    monkeypatch.setattr(planning_worker, "generate_run_checks", generate)
    planning_worker.create_run_check_suggestion(body["request_id"])
    assert calls == []
    with sql_factory() as db:
        denied = db.get(RunSetupRequest, body["request_id"])
        assert (denied.status, denied.suggestion) == ("failed", None)
        assert denied.error == planning_worker.RUN_CHECK_DENIED
    assert len(audits(sql_factory, "run_setup.denied", body["request_id"])) == 1
