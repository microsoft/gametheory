import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from durabletask import task
from durabletask.internal.orchestrator_service_pb2 import TaskFailureDetails
from pydantic import ValidationError

from gametheory.domain import ProposalContent
from gametheory.worker import plan_v1, propose_v1


def test_durable_orchestration_has_bounded_retries_and_failure_activity():
    ctx = Mock()
    first, failure = Mock(), Mock()
    ctx.call_activity.side_effect = [first, failure]
    workflow = plan_v1(ctx, "request-id")
    assert next(workflow) is first
    assert ctx.call_activity.call_args.kwargs["retry_policy"].max_number_of_attempts == 3
    assert (
        workflow.throw(
            task.TaskFailedError(
                "failed", TaskFailureDetails(errorType="TestFailure", errorMessage="failed")
            )
        )
        is failure
    )
    with pytest.raises(StopIteration):
        next(workflow)


def test_activity_does_not_leak_model_payload_into_scheduler_history(monkeypatch):
    def invalid(_):
        ProposalContent.model_validate({"secret_model_text": "private content"})

    monkeypatch.setattr("gametheory.worker.create_proposal", invalid)
    with pytest.raises(RuntimeError) as caught:
        propose_v1(Mock(), "request-id")
    assert "private content" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_model_contract_is_validatable():
    response = ProposalContent.model_validate(
        {
            "summary": "Clarify the objective",
            "content": {"title": "Exercise"},
        }
    )
    assert response.content.schema_version == 1
    with pytest.raises(ValidationError):
        ProposalContent.model_validate_json(
            '{"summary":"unsafe","content":{"title":"Exercise","execute":true}}'
        )


def test_worker_starts_before_dispatching(monkeypatch):
    from gametheory import worker as module

    stop = Mock()
    stop.is_set.side_effect = [False, True]
    client, worker = MagicMock(), MagicMock()
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: SimpleNamespace(
            planning_enabled=True,
            run_assistant_enabled=False,
            scheduler_emulator=True,
            scheduler_endpoint="http://127.0.0.1:8080",
            scheduler_taskhub="test",
        ),
    )
    monkeypatch.setattr(module.threading, "Event", lambda: stop)
    monkeypatch.setattr(module.signal, "signal", Mock())
    monkeypatch.setattr(module, "DurableTaskSchedulerClient", Mock(return_value=client))
    monkeypatch.setattr(module, "DurableTaskSchedulerWorker", Mock(return_value=worker))
    monkeypatch.setattr(module, "configure_logging", Mock())
    monkeypatch.setattr(module, "dispatch_once", lambda _: worker.start.assert_called_once())
    monkeypatch.setattr(module, "reconcile_once", Mock())
    module.main()
    worker.start.assert_called_once()
    worker.__exit__.assert_called_once()
    client.close.assert_called_once()


@pytest.mark.parametrize("fail", [False, True])
def test_foundry_owns_actual_sdk_clients_and_closes_them(monkeypatch, fail):
    from gametheory import planning
    from gametheory.domain import ScenarioContent

    real_client = planning.FoundryChatClient
    instances = []
    agent = SimpleNamespace(run=AsyncMock())
    agent.run.return_value = SimpleNamespace(
        text='{"summary":"Reviewed proposal","content":{"title":"Exercise"}}'
    )
    if fail:
        agent.run.side_effect = RuntimeError("Synthetic provider failure")

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
            planning_enabled=True,
            profile=SimpleNamespace(authority="https://login.microsoftonline.com"),
            foundry_project_endpoint="https://fixture.invalid/api/projects/test",
            model_deployment="fixture",
            model_timeout_seconds=10,
            planner_max_output_tokens=1000,
        ),
    )
    call = planning.generate_proposal(
        ScenarioContent(title="Exercise").model_dump_json(), "Clarify the objective", []
    )
    if fail:
        with pytest.raises(RuntimeError, match="Synthetic"):
            asyncio.run(call)
    else:
        assert asyncio.run(call).content.title == "Exercise"
    assert len(instances) == 1
    assert instances[0].client.is_closed()
