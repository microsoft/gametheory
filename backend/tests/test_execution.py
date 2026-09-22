import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from gametheory import execution_adapters as adapters
from gametheory import execution_worker as worker
from gametheory.domain import Objective, ScenarioContent
from gametheory.execution import ObjectiveRule, Observation, ReadinessReceipt, RunManifest, compare
from gametheory.execution_adapters import AllowedOperation, TargetBinding
from gametheory.execution_evidence import findings
from gametheory.persistence import RunEvent, RunStep
from gametheory.preparation import (
    BoardDraft,
    ConfigurationSnapshot,
    ConnectionConfiguration,
    OperationDefinition,
    PreparationManifest,
    PreparationStep,
    PreparationWindow,
    ScenarioPin,
    canonical_digest,
)


def example_manifest(*, effect="read", kind="rest", classification="nonproduction"):
    current = datetime.now(UTC)
    operation = OperationDefinition(
        key="record.read" if effect == "read" else "record.update",
        version="1",
        label="Record operation",
        effect=effect,
        invocation={
            "kind": "rest",
            "method": "GET" if effect == "read" else "POST",
            "path": "/records/{record_id}",
        }
        if kind == "rest"
        else {"kind": "sql", "procedure": "exercise.RecordOperation"},
        parameters=[{"name": "record_id", "type": "uuid"}],
        results=[
            {"name": "quantity", "type": "integer", "minimum": 0, "maximum": 100},
            {"name": "committed_at", "type": "datetime", "required": False},
            {"name": "record_version", "type": "string", "max_length": 100, "required": False},
        ],
        recovery="Explicit versioned recovery or external manual accounting.",
    )
    content = ConnectionConfiguration(
        classification=classification,
        resource_id="resource/example",
        endpoint="https://records.invalid" if kind == "rest" else "sql.example.invalid",
        database="exercise_test" if kind == "sql" else "",
        identity_ref="identity/executor",
        catalog={"name": "Test-only record catalog", "operations": [operation]},
    )
    wid = uuid4()
    config = ConfigurationSnapshot(
        id=uuid4(),
        workspace_id=wid,
        connection_id=uuid4(),
        version=1,
        content=content,
        digest=canonical_digest(content.model_dump(mode="json")),
        connection_kind=kind,
        connection_name="Test-only record system",
        environment_id=uuid4(),
        environment_name="Label is not authority",
        created_by=uuid4(),
        created_at=current,
    )
    step = PreparationStep(
        id=uuid4(),
        label="Read record",
        kind="operation",
        binding={
            "configuration_id": config.id,
            "operation_key": operation.key,
            "operation_version": "1",
        },
        parameters={"record_id": str(uuid4())},
    )
    objective = Objective(
        id=uuid4(),
        title="Quantity condition",
        criterion="Evidence must show the intended threshold.",
    )
    prep = PreparationManifest(
        board_id=uuid4(),
        workspace_id=wid,
        board_version=1,
        draft=BoardDraft(
            name="Test execution",
            steps=[step],
            window=PreparationWindow(
                starts_at=current - timedelta(minutes=1), ends_at=current + timedelta(hours=1)
            ),
            recovery="Explicit recovery only.",
        ),
        scenario=ScenarioPin(
            scenario_id=uuid4(),
            revision_version=1,
            content=ScenarioContent(title="Record exercise", objectives=[objective]),
        ),
        assets=[],
        configurations=[config],
    )
    return RunManifest(
        run_id=uuid4(),
        preparation=prep,
        trigger="manual",
        observations=[],
        objectives=[],
        recovery=[],
    )


def target(manifest):
    config = manifest.preparation.configurations[0]
    return TargetBinding(
        configuration_id=config.id,
        configuration_digest=config.digest,
        environment_id=config.environment_id,
        classification=config.content.classification,
        resource_id=config.content.resource_id,
        endpoint=config.content.endpoint,
        database=config.content.database,
        identity_ref=config.content.identity_ref,
        client_id=uuid4(),
        token_scope="api://test/.default",
        operations=[
            AllowedOperation(digest=canonical_digest(op.model_dump(mode="json")))
            for op in config.content.catalog.operations
        ],
    )


def event(step, quantity, at, **values):
    return RunEvent(
        id=str(uuid4()),
        run_id=str(uuid4()),
        kind="observation",
        step_id=str(step),
        created_at=at.replace(tzinfo=None),
        detail=json.dumps(
            {"phase": "exercise", "result": json.dumps({"quantity": quantity, **values})}
        ),
    )


def test_manifest_is_separate_immutable_and_historically_parseable():
    manifest = example_manifest(classification="production")
    document = manifest.model_dump(mode="json")
    original = manifest.digest
    document["preparation"]["draft"]["window"] = {
        "starts_at": "2020-01-01T00:00:00Z",
        "ends_at": "2020-01-01T01:00:00Z",
    }
    historical = RunManifest.model_validate(document)
    assert historical.digest != original
    assert historical.preparation.execution_authorized is False
    assert historical.schema_version == "exercise-execution/v2"
    with pytest.raises(ValidationError):
        RunManifest.model_validate(manifest.preparation.model_dump(mode="json"))


def test_write_observation_and_fabricated_objective_fields_are_rejected():
    manifest = example_manifest(effect="write")
    sid = manifest.preparation.draft.steps[0].id
    document = manifest.model_dump(mode="json")
    document["observations"] = [
        Observation(step_id=sid, field="quantity", operator="gt", value=85).model_dump(mode="json")
    ]
    with pytest.raises(ValidationError, match="read operations"):
        RunManifest.model_validate(document)
    document["observations"] = []
    document["objectives"] = [
        ObjectiveRule(
            objective_id=manifest.preparation.scenario.content.objectives[0].id,
            step_id=sid,
            field="invented",
            operator="eq",
            value="yes",
        ).model_dump(mode="json")
    ]
    with pytest.raises(ValidationError, match="declared"):
        RunManifest.model_validate(document)


def test_target_binding_cannot_retarget_identity_or_operation():
    manifest = example_manifest()
    config = manifest.preparation.configurations[0]
    binding = target(manifest)
    assert adapters.binding_for(config, {config.id: binding}) == binding
    with pytest.raises(HTTPException, match="Target or identity"):
        adapters.binding_for(
            config, {config.id: binding.model_copy(update={"identity_ref": "identity/different"})}
        )
    with pytest.raises(HTTPException, match="not authorized"):
        binding.operation(config.content.catalog.operations[0].model_copy(update={"version": "2"}))


def test_idempotency_keys_are_stable_and_isolated():
    key = adapters.operation_key("run", "exercise", "step")
    assert len(key) == 64
    assert key == adapters.operation_key("run", "exercise", "step")
    assert (
        len(
            {
                key,
                adapters.operation_key("other", "exercise", "step"),
                adapters.operation_key("run", "recovery", "step"),
                adapters.operation_key("run", "exercise", "other"),
            }
        )
        == 4
    )


def test_rest_headers_and_encoded_paths_are_dispatcher_owned():
    operation = (
        example_manifest(effect="write").preparation.configurations[0].content.catalog.operations[0]
    )
    path, headers, payload = adapters.rest_request(
        operation,
        {"record_id": "safe id", "expected_version": "v1:abc", "quantity": 2},
        "stable-key",
    )
    assert path == "/records/safe%20id"
    assert headers == {"If-Match": '"v1:abc"', "Idempotency-Key": "stable-key"}
    assert payload == {"quantity": 2}
    for malicious in ["..", "../record", "a/b", "a\\b", "%2f"]:
        with pytest.raises(ValueError):
            adapters.rest_request(operation, {"record_id": malicious}, "key")
    with pytest.raises(ValueError):
        adapters.rest_request(operation, {"record_id": "id", "expected_version": '"quoted"'}, "key")


@pytest.mark.parametrize("value", ["20", True, float("nan"), float("inf")])
def test_runtime_results_are_not_coerced(value):
    operation = example_manifest().preparation.configurations[0].content.catalog.operations[0]
    with pytest.raises(ValueError):
        adapters.output(operation, {"quantity": value})


@pytest.mark.parametrize("effect,outcome", [("write", "unknown"), ("read", "failed")])
def test_transport_failure_has_an_explicit_outcome(monkeypatch, effect, outcome):
    manifest = example_manifest(effect=effect)
    operation = manifest.preparation.configurations[0].content.catalog.operations[0]
    monkeypatch.setattr(
        adapters,
        "rest_call",
        Mock(side_effect=httpx.ReadTimeout("credential-bearing provider body")),
    )
    result = adapters.invoke(target(manifest), operation, {}, "key")
    assert result.outcome == outcome
    assert "credential-bearing" not in result.reason


def test_sql_rejection_does_not_require_success_fields_or_invent_success(monkeypatch):
    manifest = example_manifest(effect="write", kind="sql")
    operation = manifest.preparation.configurations[0].content.catalog.operations[0]
    monkeypatch.setattr(adapters, "sql_call", lambda *_: {"outcome": "rejected", "quantity": None})
    assert adapters.invoke(target(manifest), operation, {}, "key").outcome == "rejected"
    monkeypatch.setattr(
        adapters,
        "sql_call",
        Mock(side_effect=OperationalError("provider-secret", {}, Exception("token"))),
    )
    result = adapters.invoke(target(manifest), operation, {}, "key")
    assert result.outcome == "unknown"
    assert "provider-secret" not in result.reason


def test_sql_adapter_uses_autocommit_bound_values_and_verifies_database(monkeypatch):
    manifest = example_manifest(effect="write", kind="sql")
    operation = manifest.preparation.configurations[0].content.catalog.operations[0]
    engine, connection = MagicMock(), MagicMock()
    engine.connect.return_value.execution_options.return_value.__enter__.return_value = connection
    connection.scalar.return_value = "exercise_test"
    connection.execute.return_value.mappings.return_value.one.return_value = {"quantity": 42}
    monkeypatch.setattr(adapters, "create_engine", lambda *_args, **_kwargs: engine)
    monkeypatch.setattr(adapters.event, "listens_for", lambda *_: lambda callback: callback)
    assert adapters.sql_call(
        target(manifest), operation, {"record_id": "input-not-sql"}, "key"
    ) == {"quantity": 42}
    engine.connect.return_value.execution_options.assert_called_once_with(
        isolation_level="AUTOCOMMIT"
    )
    statement, params = connection.execute.call_args.args
    assert "input-not-sql" not in str(statement)
    assert params == {"record_id": "input-not-sql"}
    engine.dispose.assert_called_once()


def test_rest_refuses_redirects_and_does_not_forward_credentials(monkeypatch):
    manifest = example_manifest(effect="write")
    operation = manifest.preparation.configurations[0].content.catalog.operations[0]
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://unapproved.invalid"})

    client_type = httpx.Client
    credential = MagicMock()
    credential.__enter__.return_value = credential
    credential.get_token.return_value = SimpleNamespace(token="fixture-only")
    monkeypatch.setattr(adapters, "ManagedIdentityCredential", lambda **_: credential)
    monkeypatch.setattr(
        adapters.httpx,
        "Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = adapters.invoke(target(manifest), operation, {"record_id": "record"}, "key")
    assert result.outcome == "unknown"
    assert len(requests) == 1 and requests[0].url.host == "records.invalid"


@pytest.mark.parametrize("quantity,expected", [(85, "unmet"), (86, "met")])
def test_assessment_respects_strict_threshold(quantity, expected):
    manifest = example_manifest()
    sid = manifest.preparation.draft.steps[0].id
    manifest.objectives = [
        ObjectiveRule(
            objective_id=manifest.preparation.scenario.content.objectives[0].id,
            step_id=sid,
            field="quantity",
            operator="gt",
            value=85,
        )
    ]
    assert findings(manifest, [event(sid, quantity, datetime.now(UTC))])[0].state == expected


@pytest.mark.parametrize("offset,expected", [(120, "met"), (120.000001, "indeterminate")])
def test_observation_deadline_is_inclusive_and_late_observation_is_not_failure(offset, expected):
    manifest = example_manifest()
    sid = manifest.preparation.draft.steps[0].id
    anchor = datetime.now(UTC) - timedelta(minutes=10)
    manifest.objectives = [
        ObjectiveRule(
            objective_id=manifest.preparation.scenario.content.objectives[0].id,
            step_id=sid,
            field="quantity",
            operator="gt",
            value=85,
            anchor_step_id=sid,
            anchor_field="committed_at",
            within_seconds=120,
        )
    ]
    assert (
        findings(
            manifest,
            [event(sid, 86, anchor + timedelta(seconds=offset), committed_at=anchor.isoformat())],
        )[0].state
        == expected
    )
    assert (
        findings(manifest, [event(sid, 86, anchor + timedelta(seconds=offset))])[0].state
        == "indeterminate"
    )


def test_readiness_receipts_require_all_checks_and_explicit_lifetime():
    current = datetime.now(UTC)
    body = dict(
        configuration_id=uuid4(),
        binding_digest="a" * 64,
        checked_at=current,
        expires_at=current + timedelta(hours=1),
        evidence_reference="receipt:example",
        checks=["connectivity", "identity", "permissions", "isolation"],
    )
    ReadinessReceipt(**body)
    with pytest.raises(ValidationError):
        ReadinessReceipt(**(body | {"checks": ["connectivity"]}))
    with pytest.raises(ValidationError):
        ReadinessReceipt(**(body | {"expires_at": current}))


def test_conditions_do_not_coerce_boolean_and_numeric_values():
    assert compare(85, "gt", 85) is False
    assert compare(86, "gt", 85) is True
    with pytest.raises(ValueError):
        compare(True, "eq", 1)


def test_runtime_skips_unselected_branches_and_requires_all_explicit_dependencies():
    manifest = example_manifest()
    first = manifest.preparation.draft.steps[0]
    yes, no, condition = uuid4(), uuid4(), uuid4()
    manifest.preparation.draft.steps.extend(
        [
            PreparationStep(
                id=condition,
                label="Threshold",
                kind="condition",
                depends_on=[first.id],
                condition={
                    "source_step_id": first.id,
                    "result_field": "quantity",
                    "operator": "gt",
                    "value": 85,
                    "if_true": [yes],
                    "if_false": [no],
                },
            ),
            PreparationStep(id=yes, label="Selected wait", kind="wait", wait_seconds=1),
            PreparationStep(id=no, label="Unselected wait", kind="wait", wait_seconds=1),
        ]
    )
    manifest = RunManifest.model_validate(manifest.model_dump())
    rows = {
        str(step.id): RunStep(step_id=str(step.id), state="pending", result="{}")
        for step in manifest.preparation.draft.steps
    }
    rows[str(first.id)].state = "succeeded"
    rows[str(condition)].state, rows[str(condition)].result = "succeeded", '{"condition":true}'
    assert worker.runnable(manifest, rows)[0] == str(yes)
    rows[str(yes)].state = "succeeded"
    assert worker.runnable(manifest, rows) == (None, [str(no)])


def test_durable_wait_is_interruptible_and_has_no_effect_retry_policy(monkeypatch):
    context = Mock()
    activity, timer, control, either = Mock(), Mock(), Mock(), Mock()
    context.call_activity.return_value = activity
    context.create_timer.return_value = timer
    context.wait_for_external_event.return_value = control
    monkeypatch.setattr(
        worker.task, "when_any", lambda tasks: either if tasks == [timer, control] else None
    )
    flow = worker.exercise_v1(context, "dispatch")
    assert next(flow) is activity
    assert "retry_policy" not in context.call_activity.call_args.kwargs
    assert flow.send({"done": False, "delay": 60}) is either
    assert flow.send(control) is activity
    timer.cancel.assert_called_once()
    with pytest.raises(StopIteration):
        flow.send({"done": True, "delay": 0})
