import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from gametheory import execution_adapters as adapters
from gametheory import execution_worker as worker
from gametheory.domain import Objective, ScenarioContent
from gametheory.execution import (
    ObjectiveRule,
    Observation,
    ReadinessReceipt,
    RecoveryBinding,
    RunManifest,
    compare,
    execution_issues,
    planned_attempts,
)
from gametheory.execution_adapters import AllowedOperation, TargetBinding
from gametheory.execution_evidence import findings
from gametheory.persistence import RunEvent, RunStep
from gametheory.preparation import (
    BoardDraft,
    ConfigurationSnapshot,
    ConnectionConfiguration,
    OperationDefinition,
    OperationField,
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
    if effect == "write":
        from gametheory.preparation import OperationField

        operation.results = [
            field.model_copy(update={"required": True}) if field.name == "committed_at" else field
            for field in operation.results
        ] + [
            OperationField(name="outcome", type="string", required=True),
            OperationField(name="durable_event_id", type="uuid", required=True),
        ]
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


def test_unconfirmed_write_contract_cannot_become_an_executable_run():
    manifest = example_manifest(effect="write")
    config = manifest.preparation.configurations[0]
    config.content.catalog.operations[0].results = [
        field
        for field in config.content.catalog.operations[0].results
        if field.name != "durable_event_id"
    ]
    config.digest = canonical_digest(config.content.model_dump(mode="json"))
    with pytest.raises(ValidationError, match="receipt fields"):
        RunManifest.model_validate(manifest.model_dump())


def test_post_cannot_be_repeated_as_a_read_and_waits_fit_window():
    manifest = example_manifest()
    config = manifest.preparation.configurations[0]
    config.content.catalog.operations[0].invocation.method = "POST"
    config.digest = canonical_digest(config.content.model_dump(mode="json"))
    with pytest.raises(ValidationError, match="Only GET"):
        RunManifest.model_validate(manifest.model_dump())
    manifest = example_manifest()
    manifest.preparation.draft.steps.append(
        PreparationStep(
            id=uuid4(),
            label="Too long",
            kind="wait",
            wait_seconds=7200,
        )
    )
    with pytest.raises(ValidationError, match="Fixed waits"):
        RunManifest.model_validate(manifest.model_dump())


def test_activity_failure_keeps_an_intervention_workflow_alive(monkeypatch):
    from durabletask.internal.orchestrator_service_pb2 import TaskFailureDetails

    context = Mock()
    activity, intervention, timer, control = Mock(), Mock(), Mock(), Mock()
    context.call_activity.side_effect = [activity, intervention, activity]
    context.create_timer.return_value = timer
    context.wait_for_external_event.return_value = control
    monkeypatch.setattr(worker.task, "when_any", lambda tasks: timer)
    flow = worker.exercise_v1(context, "dispatch")
    assert next(flow) is activity
    failure = worker.task.TaskFailedError(
        "fixture", TaskFailureDetails(errorType="Fixture", errorMessage="fixture")
    )
    assert flow.throw(failure) is intervention
    assert context.call_activity.call_args.kwargs["retry_policy"].max_number_of_attempts == 3
    assert flow.send(None) is timer
    assert flow.send(timer) is activity


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


def owned_recovery(sid, config_id, key="record.read"):
    return RecoveryBinding.model_validate(
        {
            "step_id": sid,
            "binding": {
                "configuration_id": config_id,
                "operation_key": key,
                "operation_version": "1",
            },
            "parameters": {
                "record_id": {"source_step_id": sid, "field": "record_version"},
                "expected_version": {"source_step_id": sid, "field": "committed_at"},
            },
            "ownership_parameter": "record_id",
            "version_parameter": "expected_version",
        }
    )


def test_execution_issues_report_every_binding_problem_with_its_location():
    manifest = example_manifest()
    prep = manifest.preparation
    sid = prep.draft.steps[0].id
    objective = prep.scenario.content.objectives[0].id
    observations = [
        Observation(step_id=sid, field="quantity", operator="gt", value=85),
        Observation(step_id=sid, field="quantity", operator="lt", value=10),
    ]
    objectives = [
        ObjectiveRule(
            objective_id=objective, step_id=sid, field="invented", operator="eq", value=1
        ),
        ObjectiveRule(objective_id=uuid4(), step_id=sid, field="quantity", operator="eq", value=1),
    ]
    recovery = [owned_recovery(sid, prep.configurations[0].id)]
    issues = list(execution_issues(prep, observations, objectives, recovery))
    assert [(item.section, item.index, item.code) for item in issues] == [
        ("observations", 1, "duplicate_observation"),
        ("objectives", 0, "objective_reference_undeclared"),
        ("objectives", 1, "objective_unknown"),
        ("recovery", 0, "recovery_binding_invalid"),
    ]
    assert issues[1].field == "invented" and issues[1].objective_id == objective
    assert issues[3].step_id == sid
    document = manifest.model_dump(mode="json") | {
        "observations": [item.model_dump(mode="json") for item in observations],
        "objectives": [item.model_dump(mode="json") for item in objectives],
        "recovery": [item.model_dump(mode="json") for item in recovery],
    }
    with pytest.raises(ValidationError, match=issues[0].message):
        RunManifest.model_validate(document)
    assert list(execution_issues(prep, observations[:1], [], [])) == []


def sql_write_manifest():
    manifest = example_manifest(effect="write", kind="sql")
    config = manifest.preparation.configurations[0]
    operation = config.content.catalog.operations[0]
    operation.parameters.append(
        OperationField(name="idempotency_key", type="string", max_length=128)
    )
    config.digest = canonical_digest(config.content.model_dump(mode="json"))
    return manifest


@pytest.mark.parametrize(
    "change,code",
    [
        (
            lambda document: document["preparation"]["draft"].update(recovery=" "),
            "missing_recovery_decision",
        ),
        (
            lambda document: document["preparation"]["draft"]["steps"][0]["parameters"].update(
                idempotency_key="literal-from-preparation"
            ),
            "sql_idempotency_literal",
        ),
        (
            lambda document: document["preparation"]["draft"]["steps"][0]["parameters"].pop(
                "record_id"
            ),
            "unresolved_parameter",
        ),
    ],
)
def test_first_execution_issue_is_exactly_the_validator_rejection(change, code):
    document = sql_write_manifest().model_dump(mode="json")
    change(document)
    prep = PreparationManifest.model_validate(document["preparation"])
    issues = list(execution_issues(prep, [], [], []))
    assert issues[0].code == code
    assert issues[0].section == "preparation"
    if code != "missing_recovery_decision":
        assert issues[0].step_id == prep.draft.steps[0].id
    with pytest.raises(ValidationError) as rejected:
        RunManifest.model_validate(document)
    assert issues[0].message in str(rejected.value)


def test_planned_attempts_count_bounded_samples_and_recovery():
    manifest = example_manifest()
    prep = manifest.preparation
    sid = prep.draft.steps[0].id
    assert planned_attempts(prep, [], []) == 1
    observation = Observation(step_id=sid, field="quantity", operator="gt", value=1, max_samples=60)
    assert planned_attempts(prep, [observation], []) == 60
    recovery = [owned_recovery(sid, prep.configurations[0].id)]
    assert planned_attempts(prep, [observation], recovery) == 61


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
    repeated = operation.model_dump()
    repeated["invocation"]["path"] = "/records/{record_id}/references/{record_id}"
    assert (
        adapters.rest_request(
            OperationDefinition.model_validate(repeated), {"record_id": "safe id"}, "key"
        )[0]
        == "/records/safe%20id/references/safe%20id"
    )


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


def milestone_manifest():
    """Generic timing results; the engine has no knowledge of any exercise's semantics."""
    from gametheory.preparation import OperationField

    manifest = example_manifest()
    config = manifest.preparation.configurations[0]
    config.content.catalog.operations[0].results += [
        OperationField(name="created_at", type="datetime"),
        OperationField(name="acknowledged", type="boolean"),
        OperationField(name="acknowledged_at", type="datetime", required=False),
        OperationField(name="acknowledged_on_time", type="boolean", required=False),
    ]
    config.digest = canonical_digest(config.content.model_dump(mode="json"))
    return RunManifest.model_validate(manifest.model_dump())


def sample(step, at, kind="observation", **values):
    return RunEvent(
        id=str(uuid4()),
        run_id=str(uuid4()),
        kind=kind,
        step_id=str(step),
        created_at=at.replace(tzinfo=None),
        detail=json.dumps({"phase": "exercise", "result": json.dumps({"quantity": 1, **values})}),
    )


def rule(manifest, **changes):
    sid = manifest.preparation.draft.steps[0].id
    return ObjectiveRule(
        objective_id=manifest.preparation.scenario.content.objectives[0].id,
        step_id=sid,
        **changes,
    )


def test_empty_optional_verdict_stays_indeterminate_until_a_sample_decides_it():
    manifest = milestone_manifest()
    sid = manifest.preparation.draft.steps[0].id
    manifest.objectives = [rule(manifest, field="acknowledged_on_time", operator="eq", value=True)]
    current = datetime.now(UTC)
    empty = [
        sample(sid, current + timedelta(seconds=index), acknowledged_on_time=None)
        for index in range(3)
    ]
    finding = findings(manifest, empty)[0]
    assert finding.state == "indeterminate"
    assert finding.reason == "A declared optional evidence field is missing."
    assert finding.evidence_ids == []
    for decided, expected in ((True, "met"), (False, "unmet")):
        later = sample(sid, current + timedelta(seconds=9), acknowledged_on_time=decided)
        finding = findings(manifest, [*empty, later])[0]
        assert finding.state == expected
        assert finding.evidence_ids == [UUID(later.id)]


@pytest.mark.parametrize("offset,expected", [(300, "met"), (601, "unmet"), (None, "indeterminate")])
def test_duplicate_samples_of_one_source_event_do_not_change_the_finding(offset, expected):
    manifest = milestone_manifest()
    sid = manifest.preparation.draft.steps[0].id
    manifest.objectives = [
        rule(
            manifest,
            field="acknowledged",
            operator="eq",
            value=True,
            anchor_step_id=sid,
            anchor_field="created_at",
            within_seconds=600,
            source_time_field="acknowledged_at",
        )
    ]
    created = datetime.now(UTC) - timedelta(hours=1)
    values = {
        "created_at": created.isoformat(),
        "acknowledged": offset is not None,
        "acknowledged_at": (created + timedelta(seconds=offset)).isoformat() if offset else None,
    }
    observed = created + timedelta(seconds=900)
    single = findings(manifest, [sample(sid, observed, **values)])[0]
    # An observed read records the same result as operation evidence and as an observation.
    repeated = [
        sample(sid, observed, kind="operation.succeeded", **values),
        sample(sid, observed + timedelta(microseconds=5), **values),
        sample(sid, observed + timedelta(seconds=10), **values),
    ]
    finding = findings(manifest, repeated)[0]
    assert single.state == finding.state == expected
    assert len(finding.evidence_ids) == len(set(finding.evidence_ids))


def test_late_observation_of_an_on_time_authoritative_source_is_met():
    manifest = milestone_manifest()
    sid = manifest.preparation.draft.steps[0].id
    created = datetime.now(UTC) - timedelta(hours=1)
    values = {
        "created_at": created.isoformat(),
        "acknowledged": True,
        "acknowledged_at": (created + timedelta(seconds=600)).isoformat(),
    }
    late = sample(sid, created + timedelta(hours=1) - timedelta(seconds=1), **values)
    clock = {
        "field": "acknowledged",
        "operator": "eq",
        "value": True,
        "anchor_step_id": sid,
        "anchor_field": "created_at",
        "within_seconds": 600,
    }
    manifest.objectives = [rule(manifest, **clock, source_time_field="acknowledged_at")]
    assert findings(manifest, [late])[0].state == "met"
    manifest.objectives = [rule(manifest, **clock)]
    assert findings(manifest, [late])[0].state == "indeterminate"
    early_source = values | {"acknowledged_at": (created - timedelta(seconds=1)).isoformat()}
    manifest.objectives = [rule(manifest, **clock, source_time_field="acknowledged_at")]
    assert findings(manifest, [sample(sid, late.created_at, **early_source)])[0].state == (
        "indeterminate"
    )


def test_missing_anchor_evidence_is_indeterminate_not_failure():
    manifest = milestone_manifest()
    sid = manifest.preparation.draft.steps[0].id
    anchor = uuid4()
    manifest.preparation.draft.steps.append(
        PreparationStep(
            id=anchor,
            label="Anchor read",
            kind="operation",
            binding=manifest.preparation.draft.steps[0].binding,
            parameters={"record_id": str(uuid4())},
        )
    )
    manifest = RunManifest.model_validate(manifest.model_dump())
    manifest.objectives = [
        rule(
            manifest,
            field="acknowledged",
            operator="eq",
            value=True,
            anchor_step_id=anchor,
            anchor_field="created_at",
            within_seconds=600,
            source_time_field="acknowledged_at",
        )
    ]
    current = datetime.now(UTC)
    observed = sample(sid, current, acknowledged=True, acknowledged_at=current.isoformat())
    for anchor_samples in ([], [sample(anchor, current, created_at=None)]):
        finding = findings(manifest, [observed, *anchor_samples])[0]
        assert finding.state == "indeterminate"
        assert finding.reason == "The authoritative start timestamp is missing."


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
    with pytest.raises(ValueError):
        compare(True, "gt", False)
    assert compare("2026-09-22T12:00:00Z", "eq", "2026-09-22T14:00:00+02:00", "datetime")
    assert compare("2026-09-22T12:00:00.000001Z", "gt", "2026-09-22T12:00:00Z", "datetime")


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
