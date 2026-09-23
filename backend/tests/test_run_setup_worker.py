"""The planning worker's run-check lane, with an explicit test-only model fixture.

SQL behavior (conditional updates, grants, and locks) is exercised against SQL Server
in test_run_setup_sql.py; this in-memory stand-in interprets only the few statements
the lane issues so each decision can be checked without a database.
"""

import asyncio
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from uuid import uuid4

import pytest
from durabletask import task
from durabletask.internal.orchestrator_service_pb2 import TaskFailureDetails

from gametheory import planning
from gametheory import worker as module
from gametheory.persistence import Audit, Membership, RunSetupRequest, Workspace, now
from gametheory.run_setup import RunCheckContext, RunCheckSuggestion


class Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row.id if self.row else None

    def first(self):
        return (
            SimpleNamespace(workspace_id=self.row.workspace_id, actor=self.row.actor)
            if self.row
            else None
        )


class Session:
    def __init__(self, store):
        self.store = store

    def get(self, model, key):
        return self.store.rows.get((model, key))

    def add(self, row):
        assert isinstance(row, Audit)
        self.store.audits.append(row.operation)

    def scalars(self, statement):
        params = statement.compile().params
        rows = sorted(
            (
                row
                for (model, _), row in self.store.rows.items()
                if model is RunSetupRequest
                and row.board_id == params["board_id_1"]
                and row.created_at < params["created_at_1"]
            ),
            key=lambda row: row.created_at,
            reverse=True,
        )
        return SimpleNamespace(all=lambda: rows[: params["param_1"]])

    def execute(self, statement):
        assert statement.table.name == "run_setup_requests"
        params = statement.compile().params
        row = self.store.rows.get((RunSetupRequest, params["id_1"]))
        if row is None or row.status not in params["status_1"]:
            return Result(None)
        for name in ("status", "suggestion", "error", "finished_at"):
            if name in params:
                setattr(row, name, params[name])
        return Result(row)


class Store:
    def __init__(self):
        self.tenant, self.actor, self.wid, self.bid = (str(uuid4()) for _ in range(4))
        self.rows = {
            (Workspace, self.wid): SimpleNamespace(id=self.wid, organization_id=self.tenant),
            (Membership, (self.wid, self.actor)): SimpleNamespace(role="viewer"),
        }
        self.audits = []

    @contextmanager
    def begin(self):
        yield Session(self)

    def request(self, prompt="Watch occupancy until it is above 85%", **changes):
        fields = {
            "id": str(uuid4()),
            "board_id": self.bid,
            "workspace_id": self.wid,
            "preview_id": str(uuid4()),
            "preview_digest": "e" * 64,
            "actor": self.actor,
            "prompt": prompt,
            "context": RunCheckContext(
                window_seconds=3600, steps=[], objectives=[], recovery_operations=[]
            ).model_dump_json(),
            "status": "queued",
            "created_at": now(),
        }
        record = RunSetupRequest(**(fields | changes))
        self.rows[(RunSetupRequest, record.id)] = record
        return record


@pytest.fixture
def store(monkeypatch):
    fixture = Store()
    monkeypatch.setattr(module, "session_factory", lambda: fixture)
    return fixture


def fixture_model(monkeypatch, effect=None):
    calls = []

    async def generate(context, prompt, history):
        calls.append((context, prompt, history))
        if effect:
            effect()
        return RunCheckSuggestion(
            summary="Explicit test fixture, not a live model result",
            questions=["Which result shows the ticket was acknowledged?"],
        )

    monkeypatch.setattr(module, "generate_run_checks", generate)
    return calls


def test_valid_output_is_published_once_with_recent_board_history(store, monkeypatch):
    earlier = store.request(
        "Suggest checks for every goal",
        status="proposed",
        suggestion=RunCheckSuggestion(summary="Earlier summary").model_dump_json(),
        created_at=now() - timedelta(minutes=5),
    )
    failed = store.request(
        "Measure recovery", status="failed", created_at=now() - timedelta(minutes=2)
    )
    pending = store.request()
    calls = fixture_model(monkeypatch)
    assert module.create_run_check_suggestion(pending.id) == pending.id
    [(context, prompt, history)] = calls
    assert (context, prompt) == (pending.context, pending.prompt)
    assert history == [
        {"user": earlier.prompt, "status": "proposed", "assistant": "Earlier summary"},
        {"user": failed.prompt, "status": "failed", "assistant": "failed"},
    ]
    assert pending.status == "proposed" and pending.error is None and pending.finished_at
    stored = RunCheckSuggestion.model_validate_json(pending.suggestion)
    assert stored.questions == ["Which result shows the ticket was acknowledged?"]
    assert store.audits == ["run_setup.suggested"]
    # A redelivered activity for a finished request neither calls the model nor republishes.
    assert module.create_run_check_suggestion(pending.id) == pending.id
    assert len(calls) == 1 and store.audits == ["run_setup.suggested"]


def test_history_is_limited_to_the_last_six_requests(store, monkeypatch):
    for minutes in range(10, 0, -1):
        store.request(
            f"Earlier {minutes}", status="failed", created_at=now() - timedelta(minutes=minutes)
        )
    pending = store.request()
    calls = fixture_model(monkeypatch)
    module.create_run_check_suggestion(pending.id)
    assert [item["user"] for item in calls[0][2]] == [f"Earlier {n}" for n in range(6, 0, -1)]


def test_a_concurrent_publication_is_never_overwritten(store, monkeypatch):
    pending = store.request()

    def other_activity_publishes():
        pending.status, pending.suggestion = "proposed", "{}"

    fixture_model(monkeypatch, other_activity_publishes)
    module.create_run_check_suggestion(pending.id)
    assert (pending.status, pending.suggestion) == ("proposed", "{}")
    assert store.audits == []


def test_revoked_membership_fails_before_the_model_is_called(store, monkeypatch):
    pending = store.request()
    del store.rows[(Membership, (store.wid, store.actor))]
    calls = fixture_model(monkeypatch)
    module.create_run_check_suggestion(pending.id)
    assert calls == []
    assert (pending.status, pending.error) == ("failed", module.RUN_CHECK_DENIED)
    assert pending.suggestion is None and store.audits == ["run_setup.denied"]


def test_membership_revoked_during_the_model_call_discards_the_result(store, monkeypatch):
    pending = store.request()

    def revoke():
        del store.rows[(Membership, (store.wid, store.actor))]

    fixture_model(monkeypatch, revoke)
    module.create_run_check_suggestion(pending.id)
    assert (pending.status, pending.error) == ("failed", module.RUN_CHECK_DENIED)
    assert pending.suggestion is None and store.audits == ["run_setup.denied"]


def foundry_returning(monkeypatch, text):
    real_client = planning.FoundryChatClient
    instances = []
    agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(text=text)))

    def construct(**kwargs):
        client = real_client(**kwargs)
        instances.append(client)
        return client

    monkeypatch.setattr(real_client, "as_agent", lambda *_args, **_kwargs: agent)
    monkeypatch.setattr(planning, "FoundryChatClient", construct)
    monkeypatch.setattr(
        planning,
        "get_settings",
        lambda: SimpleNamespace(
            run_assistant_enabled=True,
            profile=SimpleNamespace(authority="https://login.microsoftonline.com"),
            foundry_project_endpoint="https://fixture.invalid/api/projects/test",
            model_deployment="fixture",
            model_timeout_seconds=10,
            planner_max_output_tokens=1000,
        ),
    )
    return agent, instances


def test_foundry_run_checks_are_validated_and_clients_closed(monkeypatch):
    agent, instances = foundry_returning(
        monkeypatch, '{"summary":"Fixture suggestion","questions":["How is recovery timed?"]}'
    )
    result = asyncio.run(planning.generate_run_checks("{}", "Suggest checks", []))
    assert result.questions == ["How is recovery timed?"]
    assert instances[0].client.is_closed()
    options = agent.run.call_args.kwargs["options"]
    assert options["response_format"] == {"type": "json_object"} and options["store"] is False


@pytest.mark.parametrize(
    "text",
    [
        "not json: SECRET provider body",
        '{"summary":"SECRET","recipients":["lead@example.invalid"]}',
        '{"summary":"SECRET","observations":[{"step_id":"x"}]}',
    ],
)
def test_invalid_model_output_fails_with_a_safe_message(store, monkeypatch, text):
    foundry_returning(monkeypatch, text)
    with pytest.raises(planning.InvalidRunCheckOutput) as caught:
        asyncio.run(planning.generate_run_checks("{}", "Suggest checks", []))
    assert "SECRET" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    pending = store.request()
    assert module.create_run_check_suggestion(pending.id) == pending.id
    assert (pending.status, pending.error) == ("failed", module.RUN_CHECK_OUTPUT_ERROR)
    assert "SECRET" not in pending.error and pending.suggestion is None
    assert store.audits == ["run_setup.failed"]


def test_disabled_assistant_never_calls_the_model(monkeypatch):
    monkeypatch.setattr(
        planning, "get_settings", lambda: SimpleNamespace(run_assistant_enabled=False)
    )
    with pytest.raises(ValueError, match="disabled"):
        asyncio.run(planning.generate_run_checks("{}", "Suggest checks", []))


def test_activity_does_not_leak_model_payload_into_scheduler_history(monkeypatch):
    def invalid(_):
        RunCheckSuggestion.model_validate({"summary": "private content", "secret": True})

    monkeypatch.setattr(module, "create_run_check_suggestion", invalid)
    with pytest.raises(RuntimeError) as caught:
        module.propose_run_checks_v1(Mock(), "request-id")
    assert "private content" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_orchestration_is_a_new_version_with_bounded_retries_and_a_failure_activity():
    ctx = Mock()
    first, failure = Mock(), Mock()
    ctx.call_activity.side_effect = [first, failure]
    workflow = module.suggest_run_checks_v1(ctx, "request-id")
    assert next(workflow) is first
    assert ctx.call_activity.call_args.args[0] is module.propose_run_checks_v1
    assert ctx.call_activity.call_args.kwargs["retry_policy"].max_number_of_attempts == 3
    assert (
        workflow.throw(
            task.TaskFailedError(
                "failed", TaskFailureDetails(errorType="TestFailure", errorMessage="failed")
            )
        )
        is failure
    )
    assert ctx.call_activity.call_args.args[0] is module.fail_run_checks_v1
    with pytest.raises(StopIteration):
        next(workflow)
    assert module.run_check_instance("abc") == "run-setup-abc"
    assert module.planning_orchestrator is module.plan_v1


def test_failure_activity_closes_only_an_active_request(store):
    pending = store.request()
    module.fail_run_checks_v1(Mock(), pending.id)
    assert pending.status == "failed" and "bounded retries" in pending.error
    module.fail_run_checks_v1(Mock(), pending.id)
    assert store.audits == ["run_setup.failed"]


@pytest.mark.parametrize("enabled", [False, True])
def test_worker_registers_the_new_version_and_dispatches_it_only_when_enabled(monkeypatch, enabled):
    stop = Mock()
    stop.is_set.side_effect = [False, True]
    client, worker = MagicMock(), MagicMock()
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            planning_enabled=True,
            run_assistant_enabled=enabled,
            scheduler_emulator=True,
            scheduler_endpoint="http://127.0.0.1:8080",
            scheduler_taskhub="test",
        ),
    )
    lanes = MagicMock()
    monkeypatch.setattr(module.threading, "Event", lambda: stop)
    monkeypatch.setattr(module.signal, "signal", Mock())
    monkeypatch.setattr(module, "DurableTaskSchedulerClient", Mock(return_value=client))
    monkeypatch.setattr(module, "DurableTaskSchedulerWorker", Mock(return_value=worker))
    monkeypatch.setattr(module, "configure_logging", Mock())
    for name in (
        "dispatch_once",
        "reconcile_once",
        "dispatch_run_checks_once",
        "reconcile_run_checks_once",
    ):
        monkeypatch.setattr(module, name, getattr(lanes, name))
    module.main()
    orchestrators = [call.args[0] for call in worker.add_orchestrator.call_args_list]
    activities = [call.args[0] for call in worker.add_activity.call_args_list]
    assert orchestrators == [module.plan_v1, module.suggest_run_checks_v1]
    assert activities == [
        module.propose_v1,
        module.fail_v1,
        module.propose_run_checks_v1,
        module.fail_run_checks_v1,
    ]
    lanes.dispatch_once.assert_called_once_with(client)
    assert lanes.dispatch_run_checks_once.called is enabled
    assert lanes.reconcile_run_checks_once.called is enabled
