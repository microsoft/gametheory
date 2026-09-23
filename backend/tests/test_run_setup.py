"""Run-check suggestion contracts, minimized model context, and per-item review."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gametheory.api import app
from gametheory.auth import Principal, authenticate
from gametheory.config import Settings
from gametheory.execution import RunCreate, RunManifest
from gametheory.persistence import get_db
from gametheory.preparation import (
    ConfigurationSnapshot,
    ConnectionConfiguration,
    PreparationManifest,
    canonical_digest,
)
from gametheory.run_setup import (
    NOTIFICATION_MESSAGE,
    RunCheckRequestInput,
    RunCheckSuggestion,
    conditional_steps,
    review_suggestion,
    run_check_context,
)

GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures" / "guided-run-setup.json").read_text(encoding="utf-8")
)


def golden_manifest(**draft_changes: object) -> PreparationManifest:
    document = json.loads(json.dumps(GOLDEN["preview_manifest"]))
    document["draft"].update(draft_changes)
    return PreparationManifest.model_validate(document)


def golden_suggestion(**changes: object) -> RunCheckSuggestion:
    bindings = GOLDEN["run_create"]
    return RunCheckSuggestion.model_validate(
        {
            "summary": "Watch occupancy and judge both goals from recorded results.",
            "observations": bindings["observations"],
            "objectives": bindings["objectives"],
            "recovery": bindings["recovery"],
            "questions": [],
        }
        | changes
    )


def step_ids() -> dict[str, str]:
    steps = GOLDEN["preview_manifest"]["draft"]["steps"]
    return {step["label"]: step["id"] for step in steps}


def test_context_contains_only_declarations_objectives_and_registered_writes():
    manifest = golden_manifest()
    context = run_check_context(manifest)
    assert context.window_seconds == 7200
    assert [step.label for step in context.steps] == [
        "Raise occupancy",
        "Read occupancy",
        "Open resource ticket",
        "Read ticket",
    ]
    raise_step, read_step = context.steps[0], context.steps[1]
    assert read_step.depends_on == [raise_step.id]
    assert raise_step.operation and raise_step.operation.effect == "write"
    assert [field.name for field in raise_step.operation.parameters] == ["shelter_id", "occupancy"]
    occupancy = raise_step.operation.parameters[1]
    assert (occupancy.type, occupancy.minimum, occupancy.maximum) == ("integer", 0, 500)
    assert read_step.operation and [
        (field.name, field.required) for field in read_step.operation.results
    ] == [("occupancy_percent", True), ("committed_at", False)]
    assert not any(step.may_not_run for step in context.steps)
    assert [(item.title, item.criterion) for item in context.objectives] == [
        (item.title, item.criterion) for item in manifest.scenario.content.objectives
    ]
    assert [
        (str(item.configuration_id), item.key, item.version) for item in context.recovery_operations
    ] == [
        (str(manifest.configurations[0].id), "capacity.update", "2"),
        (str(manifest.configurations[1].id), "ticket.create", "1"),
        (str(manifest.configurations[1].id), "ticket.close", "1"),
    ]
    document = json.loads(context.model_dump_json())
    assert set(document) == {
        "schema_version",
        "window_seconds",
        "steps",
        "objectives",
        "recovery_operations",
    }
    for step in document["steps"]:
        assert set(step) == {"id", "label", "kind", "depends_on", "may_not_run", "operation"}
        assert set(step["operation"]) == {"label", "effect", "parameters", "results"}
        for field in step["operation"]["parameters"] + step["operation"]["results"]:
            assert set(field) == {"name", "type", "required", "choices", "minimum", "maximum"}
    for operation in document["recovery_operations"]:
        assert set(operation) == {
            "configuration_id",
            "key",
            "version",
            "label",
            "parameters",
            "results",
        }


def test_context_never_includes_target_metadata_values_or_assets():
    manifest = golden_manifest()
    text = run_check_context(manifest).model_dump_json()
    excluded = [str(manifest.board_id), str(manifest.workspace_id), "idempotency_key"]
    for config in manifest.configurations:
        content = config.content
        excluded += [
            content.resource_id,
            content.endpoint,
            content.identity_ref,
            config.connection_name,
            config.environment_name,
            str(config.environment_id),
            str(config.connection_id),
            str(config.created_by),
            config.digest,
            content.catalog.name,
        ]
        if content.database:
            excluded.append(content.database)
        for operation in content.catalog.operations:
            excluded.append(operation.recovery)
            invocation = operation.invocation
            excluded.append(invocation.procedure if invocation.kind == "sql" else invocation.path)
    for step in manifest.draft.steps:
        excluded += [value for value in step.parameters.values() if isinstance(value, str)]
    excluded += ["EXERCISE ONLY", "108", "api://", ".default"]
    for value in excluded:
        assert value not in text, value


def test_context_marks_branch_steps_that_may_not_run():
    ids = step_ids()
    steps = json.loads(json.dumps(GOLDEN["preview_manifest"]["draft"]["steps"]))
    decision = str(uuid4())
    steps.insert(
        2,
        {
            "id": decision,
            "label": "Is the shelter over capacity?",
            "kind": "condition",
            "depends_on": [ids["Read occupancy"]],
            "condition": {
                "source_step_id": ids["Read occupancy"],
                "result_field": "occupancy_percent",
                "operator": "gt",
                "value": 85,
                "if_true": [ids["Open resource ticket"]],
            },
        },
    )
    manifest = golden_manifest(steps=steps)
    assert {str(item) for item in conditional_steps(manifest.draft.steps)} == {
        ids["Open resource ticket"],
        ids["Read ticket"],
    }
    context = run_check_context(manifest)
    flags = {step.label: step.may_not_run for step in context.steps}
    assert flags == {
        "Raise occupancy": False,
        "Read occupancy": False,
        "Is the shelter over capacity?": False,
        "Open resource ticket": True,
        "Read ticket": True,
    }
    assert context.steps[2].operation is None


def test_suggestion_contract_is_bounded_and_has_no_authority_fields():
    assert golden_suggestion().summary
    rejected = [
        {"recipients": ["lead@example.invalid"]},
        {"trigger": "scheduled"},
        {"approve": True},
        {"summary": ""},
        {"summary": "x" * 2001},
        {"questions": ["?"] * 51},
        {"questions": ["x" * 1001]},
        {"questions": [""]},
        {"observations": [GOLDEN["run_create"]["observations"][0] | {"notify": True}]},
        {"observations": [GOLDEN["run_create"]["observations"][0]] * 101},
    ]
    for change in rejected:
        with pytest.raises(ValidationError):
            golden_suggestion(**change)
    recovery = json.loads(json.dumps(GOLDEN["run_create"]["recovery"][0]))
    recovery["parameters"]["idempotency_key"] = "model-chosen"
    with pytest.raises(ValidationError, match="dispatcher-owned"):
        golden_suggestion(recovery=[recovery])
    objective = GOLDEN["run_create"]["objectives"][0] | {"within_seconds": None}
    with pytest.raises(ValidationError, match="anchor step"):
        golden_suggestion(objectives=[objective])
    with pytest.raises(ValidationError):
        RunCheckSuggestion.model_validate_json('{"summary": "unsafe", "content": {}}')


def test_every_golden_item_is_valid_against_the_pinned_preview():
    review = review_suggestion(golden_manifest(), golden_suggestion())
    items = review.observations + review.objectives + review.recovery
    assert len(items) == 4
    assert all(item.valid and item.issues == [] for item in items)


def test_review_locates_invalid_items_without_hiding_valid_ones():
    ids = step_ids()
    observation = GOLDEN["run_create"]["observations"][0]
    objective = GOLDEN["run_create"]["objectives"][0]
    recovery = json.loads(json.dumps(GOLDEN["run_create"]["recovery"][0]))
    recovery["binding"]["operation_key"] = "ticket.invented"
    review = review_suggestion(
        golden_manifest(),
        golden_suggestion(
            observations=[
                observation,
                observation,
                observation | {"step_id": ids["Read ticket"], "field": "invented"},
                observation | {"step_id": ids["Raise occupancy"]},
            ],
            objectives=[objective, objective | {"objective_id": str(uuid4())}],
            recovery=[recovery],
        ),
    )
    assert [item.valid for item in review.observations] == [True, False, False, False]
    assert review.observations[1].issues == ["Only one observation policy is allowed per read step"]
    assert review.observations[2].issues == ["Observation must compare a declared result"]
    assert review.observations[3].issues == ["Only registered read operations may be polled"]
    assert [item.valid for item in review.objectives] == [True, False]
    assert review.objectives[1].issues == ["Assessment must reference a pinned scenario objective"]
    assert review.recovery[0].issues == [
        "Recovery must bind a registered write for a recorded write"
    ]


def notify_manifest() -> tuple[PreparationManifest, str]:
    document = json.loads(json.dumps(GOLDEN["preview_manifest"]))
    base = ConfigurationSnapshot.model_validate(document["configurations"][1])
    content = ConnectionConfiguration.model_validate(
        {
            "classification": "nonproduction",
            "resource_id": "fixture/graph",
            "endpoint": "https://graph.fixture.invalid",
            "identity_ref": "fixture/graph/sender",
            "catalog": {
                "name": "Fixed notices",
                "operations": [
                    {
                        "key": "notice.send",
                        "version": "1",
                        "label": "Send the fixed notice",
                        "effect": "notify",
                        "invocation": {"kind": "graph", "template_key": "notice"},
                        "parameters": [],
                        "results": [{"name": "sent_at", "type": "datetime"}],
                        "recovery": "A sent notice cannot be undone.",
                    }
                ],
            },
        }
    )
    graph = base.model_copy(
        update={
            "id": uuid4(),
            "connection_id": uuid4(),
            "connection_kind": "graph",
            "connection_name": "Notices",
            "content": content,
            "digest": canonical_digest(content.model_dump(mode="json")),
        }
    )
    notify = str(uuid4())
    document["draft"]["steps"].append(
        {
            "id": notify,
            "label": "Notify the shelter lead",
            "kind": "operation",
            "depends_on": [document["draft"]["steps"][0]["id"]],
            "binding": {
                "configuration_id": str(graph.id),
                "operation_key": "notice.send",
                "operation_version": "1",
            },
        }
    )
    document["configurations"].append(graph.model_dump(mode="json"))
    return PreparationManifest.model_validate(document), notify


def test_items_that_use_a_notification_step_are_never_valid():
    manifest, notify = notify_manifest()
    assert "Fixed notices" not in run_check_context(manifest).model_dump_json()
    objective = GOLDEN["run_create"]["objectives"][1] | {
        "anchor_step_id": notify,
        "anchor_field": "sent_at",
    }
    observation = GOLDEN["run_create"]["observations"][0] | {"step_id": notify, "field": "sent_at"}
    review = review_suggestion(
        manifest, golden_suggestion(objectives=[objective], observations=[observation])
    )
    assert review.objectives[0].valid is False
    assert review.objectives[0].issues == [NOTIFICATION_MESSAGE]
    assert review.observations[0].issues[0] == NOTIFICATION_MESSAGE


def test_request_prompts_are_bounded_in_sql_server_units():
    body = {"preview_id": str(uuid4()), "preview_digest": "a" * 64, "request_id": str(uuid4())}
    assert RunCheckRequestInput.model_validate(body | {"prompt": "é" * 4000})
    assert RunCheckRequestInput.model_validate(body | {"prompt": "\U0001f30a" * 2000})
    for prompt in ("", "   \n\t", "x" * 4001, "\U0001f30a" * 2001):
        with pytest.raises(ValidationError):
            RunCheckRequestInput.model_validate(body | {"prompt": prompt})


def test_assistant_requires_planning_and_is_off_by_default():
    assert Settings(_env_file=None).run_assistant_enabled is False
    with pytest.raises(ValueError, match="enable and configure planning first"):
        Settings(_env_file=None, run_assistant_enabled=True)


def test_provenance_is_a_run_create_field_and_never_part_of_the_manifest():
    assert "suggestion_id" in RunCreate.model_fields
    assert set(RunManifest.model_fields) == {
        "schema_version",
        "run_id",
        "preparation",
        "trigger",
        "observations",
        "objectives",
        "recovery",
        "sql_idempotency",
        "max_operations",
    }
    assert "suggestion_id" not in json.dumps(RunManifest.model_json_schema())


@pytest.fixture
def api_client(monkeypatch):
    actor = Principal(str(uuid4()), str(uuid4()))
    app.dependency_overrides[authenticate] = lambda: actor
    app.dependency_overrides[get_db] = lambda: MagicMock()
    board = SimpleNamespace(id=str(uuid4()), version=3)
    monkeypatch.setattr("gametheory.run_setup_api.board_record", MagicMock(return_value=board))
    try:
        with TestClient(app) as client:
            yield client, board, monkeypatch
    finally:
        app.dependency_overrides.clear()


def request_body():
    return {
        "preview_id": str(uuid4()),
        "preview_digest": "a" * 64,
        "prompt": "Suggest checks for every goal.",
        "request_id": str(uuid4()),
    }


def test_assistant_api_requires_an_operator_and_reports_when_it_is_off(api_client):
    client, board, monkeypatch = api_client
    path = f"/api/workspaces/{uuid4()}/boards/{board.id}/run-setup/suggestions"
    monkeypatch.setattr("gametheory.run_setup_api.grant_record", MagicMock(return_value=None))
    assert client.get(path).status_code == 403
    assert client.post(path, json=request_body()).status_code == 403
    monkeypatch.setattr(
        "gametheory.run_setup_api.grant_record", MagicMock(return_value=SimpleNamespace(id="g"))
    )
    monkeypatch.setattr(
        "gametheory.run_setup_api.get_settings",
        lambda: SimpleNamespace(run_assistant_enabled=False),
    )
    for response in (client.get(path), client.post(path, json=request_body())):
        assert response.status_code == 503
        assert "forms remain available" in response.json()["detail"]
    assert client.post(path, json=request_body() | {"prompt": " "}).status_code == 422
    assert client.get("/api/config").json()["capabilities"]["run_assistant"] is False


def test_openapi_documents_reviewed_items_and_provenance():
    schemas = app.openapi()["components"]["schemas"]
    view = schemas["RunCheckSuggestionView"]["properties"]
    assert {"summary", "questions", "is_current", "observations", "recovery"} <= set(view)
    assert "suggestion_id" in schemas["RunCreate"]["properties"]
    assert "suggestion_id" not in json.dumps(schemas["RunManifest"])
