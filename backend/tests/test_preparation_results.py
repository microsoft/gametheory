import copy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from gametheory.domain import ScenarioContent
from gametheory.preparation import (
    BoardDraft,
    ConfigurationSnapshot,
    ConnectionConfiguration,
    ExecutionManifest,
    OperationCatalog,
    PreparationManifest,
    PriorResultReference,
    canonical_digest,
    preparation_findings,
)


def result_manifest_data():
    workspace, configuration, create, read = [str(uuid4()) for _ in range(4)]
    fields = [
        {"name": "record_id", "type": "uuid"},
        {"name": "alternate_id", "type": "uuid"},
        {"name": "record_version", "type": "string", "max_length": 64},
        {"name": "quantity", "type": "integer"},
    ]
    content = ConnectionConfiguration(
        classification="nonproduction",
        resource_id="registry/example",
        endpoint="https://records.example.invalid",
        identity_ref="identity/example",
        catalog=OperationCatalog.model_validate(
            {
                "name": "Record registry",
                "operations": [
                    {
                        "key": "record.create",
                        "version": "1",
                        "label": "Create owned record",
                        "effect": "write",
                        "invocation": {"kind": "rest", "method": "POST", "path": "/records"},
                        "parameters": [],
                        "results": fields,
                        "recovery": "Authorized owner reviews record ownership before manual recovery.",
                    },
                    {
                        "key": "record.read",
                        "version": "1",
                        "label": "Observe owned record",
                        "effect": "read",
                        "invocation": {
                            "kind": "rest",
                            "method": "GET",
                            "path": "/records/{record_id}",
                        },
                        "parameters": [
                            {"name": "record_id", "type": "uuid"},
                            {"name": "expected_version", "type": "string", "max_length": 64},
                        ],
                        "results": fields,
                        "recovery": "Read-only operation.",
                    },
                ],
            }
        ),
    )
    snapshot = ConfigurationSnapshot.model_validate(
        {
            "id": configuration,
            "workspace_id": workspace,
            "connection_id": str(uuid4()),
            "version": 1,
            "content": content,
            "digest": canonical_digest(content.model_dump(mode="json")),
            "connection_kind": "rest",
            "connection_name": "Example registry",
            "environment_id": str(uuid4()),
            "environment_name": "Test",
            "created_by": str(uuid4()),
            "created_at": datetime.now(UTC),
        }
    )
    start = datetime.now(UTC) + timedelta(hours=1)
    return {
        "board_id": str(uuid4()),
        "workspace_id": workspace,
        "board_version": 1,
        "draft": {
            "name": "Declared result preparation",
            "steps": [
                {
                    "id": create,
                    "kind": "operation",
                    "label": "Create record",
                    "binding": {
                        "configuration_id": configuration,
                        "operation_key": "record.create",
                        "operation_version": "1",
                    },
                },
                {
                    "id": read,
                    "kind": "operation",
                    "label": "Read returned record",
                    "depends_on": [create],
                    "binding": {
                        "configuration_id": configuration,
                        "operation_key": "record.read",
                        "operation_version": "1",
                    },
                    "parameters": {
                        "record_id": {"source_step_id": create, "field": "record_id"},
                        "expected_version": {"source_step_id": create, "field": "record_version"},
                    },
                },
            ],
            "window": {
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(hours=1)).isoformat(),
            },
            "recovery": "An authorized owner reviews ownership and expected versions before manual recovery.",
        },
        "scenario": {
            "scenario_id": str(uuid4()),
            "revision_version": 1,
            "content": ScenarioContent(title="Published registry scenario").model_dump(mode="json"),
        },
        "assets": [],
        "configurations": [snapshot.model_dump(mode="json")],
    }


def reseal_configuration(data):
    snapshot = data["configurations"][0]
    snapshot["content"] = ConnectionConfiguration.model_validate(snapshot["content"]).model_dump(
        mode="json"
    )
    snapshot["digest"] = canonical_digest(snapshot["content"])


def literal_read(data):
    step = copy.deepcopy(data["draft"]["steps"][1])
    step.update(id=str(uuid4()), label="Read a supplied record", depends_on=[])
    step["parameters"] = {"record_id": str(uuid4()), "expected_version": "record-v1"}
    return step


def conditional_branches(*, same_branch=False, explicit_dependency=False):
    data = result_manifest_data()
    create, read = data["draft"]["steps"]
    root = literal_read(data)
    condition = {
        "id": str(uuid4()),
        "kind": "condition",
        "label": "Compare declared quantity",
        "depends_on": [root["id"]],
        "condition": {
            "source_step_id": root["id"],
            "result_field": "quantity",
            "operator": "gte",
            "value": 1,
            "if_true": [create["id"]],
            "if_false": [read["id"]],
        },
    }
    steps = [root, condition, create, read]
    if same_branch:
        fallback = {"id": str(uuid4()), "label": "Fallback wait", "kind": "wait", "wait_seconds": 1}
        condition["condition"]["if_true"].append(read["id"])
        condition["condition"]["if_false"] = [fallback["id"]]
        steps.append(fallback)
    read["depends_on"] = [create["id"]] if explicit_dependency else []
    data["draft"]["steps"] = steps
    return data


def test_create_then_observe_keeps_typed_references_without_inventing_identifiers():
    data = result_manifest_data()
    manifest = PreparationManifest.model_validate(data)
    value = manifest.draft.steps[1].parameters["record_id"]
    assert isinstance(value, PriorResultReference)
    assert value.source_step_id == manifest.draft.steps[0].id
    assert value.field == "record_id"
    frozen = manifest.model_dump(mode="json")
    assert frozen["draft"]["steps"][1]["parameters"] == data["draft"]["steps"][1]["parameters"]
    assert PreparationManifest.model_validate_json(manifest.model_dump_json()) == manifest
    findings = preparation_findings(manifest)
    assert len([item for item in findings if item.code == "result_binding_unverified"]) == 2
    assert not [item for item in findings if item.code == "missing_parameter"]
    assert manifest.execution_authorized is False
    data["draft"]["steps"].reverse()
    PreparationManifest.model_validate(data)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_source",
        "self",
        "future_or_parallel",
        "unbound_source",
        "undeclared_output",
        "wrong_type",
        "not_an_operation",
        "expression",
        "wrong_reference_shape",
    ],
)
def test_result_references_are_local_declared_typed_and_ordered(mutation):
    data = result_manifest_data()
    create, read = data["draft"]["steps"]
    reference = read["parameters"]["record_id"]
    if mutation == "missing_source":
        reference["source_step_id"] = str(uuid4())
    elif mutation == "self":
        reference["source_step_id"] = read["id"]
    elif mutation == "future_or_parallel":
        read["depends_on"] = []
    elif mutation == "unbound_source":
        create["binding"] = None
    elif mutation == "undeclared_output":
        reference["field"] = "not_declared"
    elif mutation == "wrong_type":
        reference["field"] = "quantity"
    elif mutation == "not_an_operation":
        create.update(kind="wait", binding=None, wait_seconds=1)
    elif mutation == "expression":
        reference["field"] = "results.record_id()"
    else:
        reference["expression"] = "evaluate"
    with pytest.raises(ValidationError):
        PreparationManifest.model_validate(data)


def test_result_types_do_not_implicitly_coerce_integer_outputs_to_other_types():
    data = result_manifest_data()
    data["configurations"][0]["content"]["catalog"]["operations"][1]["parameters"][0]["type"] = (
        "number"
    )
    reseal_configuration(data)
    data["draft"]["steps"][1]["parameters"]["record_id"]["field"] = "quantity"
    with pytest.raises(ValidationError, match="exactly match"):
        PreparationManifest.model_validate(data)


def test_reference_must_still_target_a_declared_destination_parameter():
    data = result_manifest_data()
    data["draft"]["steps"][1]["parameters"]["undeclared"] = {
        "source_step_id": data["draft"]["steps"][0]["id"],
        "field": "record_id",
    }
    with pytest.raises(ValidationError, match="Parameters must be declared"):
        PreparationManifest.model_validate(data)


@pytest.mark.parametrize("explicit_dependency", [False, True])
def test_mutually_exclusive_branch_siblings_cannot_provide_each_others_results(explicit_dependency):
    data = conditional_branches(explicit_dependency=explicit_dependency)
    with pytest.raises(ValidationError):
        PreparationManifest.model_validate(data)


def test_same_branch_parallel_steps_need_an_explicit_dependency():
    with pytest.raises(ValidationError, match="guaranteed earlier"):
        PreparationManifest.model_validate(conditional_branches(same_branch=True))
    PreparationManifest.model_validate(
        conditional_branches(same_branch=True, explicit_dependency=True)
    )


def test_all_of_dependencies_accept_results_from_either_parallel_prerequisite():
    data = result_manifest_data()
    first, read = data["draft"]["steps"]
    second = copy.deepcopy(first)
    second["id"] = str(uuid4())
    data["draft"]["steps"].insert(1, second)
    read["depends_on"] = [first["id"], second["id"]]
    read["parameters"]["expected_version"]["source_step_id"] = second["id"]
    PreparationManifest.model_validate(data)
    wait = {
        "id": str(uuid4()),
        "label": "Bounded observation window",
        "kind": "wait",
        "wait_seconds": 60,
        "depends_on": [read["id"]],
    }
    final = copy.deepcopy(read)
    final.update(id=str(uuid4()), depends_on=[wait["id"]])
    data["draft"]["steps"].extend([wait, final])
    PreparationManifest.model_validate(data)


def alternate_entries(*, condition_consumer=False, explicit_source_dependency=False):
    data = result_manifest_data()
    first, second = literal_read(data), literal_read(data)
    consumer = copy.deepcopy(data["draft"]["steps"][1])
    consumer["depends_on"] = [first["id"]] if explicit_source_dependency else []
    consumer["parameters"] = {
        "record_id": {"source_step_id": first["id"], "field": "record_id"},
        "expected_version": {"source_step_id": first["id"], "field": "record_version"},
    }
    terminal = {"id": str(uuid4()), "kind": "wait", "label": "Terminal", "wait_seconds": 1}
    if condition_consumer:
        consumer.update(
            kind="condition",
            binding=None,
            parameters={},
            condition={
                "source_step_id": first["id"],
                "result_field": "quantity",
                "operator": "gte",
                "value": 1,
                "if_true": [terminal["id"]],
                "if_false": [],
            },
        )
    steps = [first, second, consumer, terminal]
    for source in (first, second):
        fallback = {
            "id": str(uuid4()),
            "kind": "wait",
            "label": "No observation",
            "wait_seconds": 1,
        }
        condition = {
            "id": str(uuid4()),
            "kind": "condition",
            "label": "Alternative entry",
            "depends_on": [source["id"]],
            "condition": {
                "source_step_id": source["id"],
                "result_field": "quantity",
                "operator": "gte",
                "value": 1,
                "if_true": [consumer["id"]],
                "if_false": [fallback["id"]],
            },
        }
        steps.extend([condition, fallback])
    data["draft"]["steps"] = steps
    return data


@pytest.mark.parametrize("condition_consumer", [False, True])
def test_one_reachable_path_does_not_guarantee_source_for_alternative_entry(condition_consumer):
    with pytest.raises(ValidationError, match="guaranteed earlier"):
        PreparationManifest.model_validate(alternate_entries(condition_consumer=condition_consumer))
    PreparationManifest.model_validate(
        alternate_entries(condition_consumer=condition_consumer, explicit_source_dependency=True)
    )


def test_guard_conflicting_all_of_join_is_rejected_even_without_result_parameters():
    data = conditional_branches()
    left, right = data["draft"]["steps"][2:4]
    right["parameters"] = {"record_id": str(uuid4()), "expected_version": "record-v1"}
    join = {
        "id": str(uuid4()),
        "label": "Impossible join",
        "kind": "wait",
        "wait_seconds": 1,
        "depends_on": [left["id"], right["id"]],
    }
    data["draft"]["steps"].append(join)
    with pytest.raises(ValidationError, match="mutually exclusive"):
        BoardDraft.model_validate(data["draft"])


def test_alternative_source_paths_cannot_hide_a_mutually_exclusive_dependency():
    data = result_manifest_data()
    source, consumer = data["draft"]["steps"]
    roots = [literal_read(data), literal_read(data)]
    steps = [source, consumer, *roots]
    consumer["depends_on"] = [source["id"]]
    for root in roots:
        fallback = {"id": str(uuid4()), "kind": "wait", "label": "False branch", "wait_seconds": 1}
        condition = {
            "id": str(uuid4()),
            "kind": "condition",
            "label": "Alternative source entry",
            "depends_on": [root["id"]],
            "condition": {
                "source_step_id": root["id"],
                "result_field": "quantity",
                "operator": "gte",
                "value": 1,
                "if_true": [source["id"]],
                "if_false": [fallback["id"]],
            },
        }
        steps.extend([condition, fallback])
        consumer["depends_on"].append(fallback["id"])
    data["draft"]["steps"] = steps
    with pytest.raises(ValidationError, match="mutually exclusive"):
        PreparationManifest.model_validate(data)


def test_conditional_path_analysis_is_bounded_without_executing_conditions():
    root = {"id": str(uuid4()), "kind": "operation", "label": "Unbound source"}
    steps = [root]
    merge_ids = []
    for _ in range(9):
        merge = {
            "id": str(uuid4()),
            "kind": "wait",
            "label": "Alternative entry",
            "wait_seconds": 1,
        }
        merge_ids.append(merge["id"])
        steps.append(merge)
        for _ in range(2):
            fallback = {
                "id": str(uuid4()),
                "kind": "wait",
                "label": "Unselected entry",
                "wait_seconds": 1,
            }
            condition = {
                "id": str(uuid4()),
                "kind": "condition",
                "label": "Declarative condition",
                "depends_on": [root["id"]],
                "condition": {
                    "source_step_id": root["id"],
                    "result_field": "quantity",
                    "operator": "eq",
                    "value": 1,
                    "if_true": [merge["id"]],
                    "if_false": [fallback["id"]],
                },
            }
            steps.extend([condition, fallback])
    steps.append(
        {
            "id": str(uuid4()),
            "kind": "wait",
            "label": "All alternatives",
            "wait_seconds": 1,
            "depends_on": merge_ids,
        }
    )
    with pytest.raises(ValidationError, match="alternative conditional paths"):
        BoardDraft.model_validate({"name": "Bounded static analysis", "steps": steps})


@pytest.mark.parametrize("change", ["source", "field", "literal"])
def test_reference_source_field_and_binding_mode_are_material_digest_changes(change):
    data = result_manifest_data()
    create, read = data["draft"]["steps"]
    another = copy.deepcopy(create)
    another["id"] = str(uuid4())
    data["draft"]["steps"].insert(1, another)
    read["depends_on"].append(another["id"])
    before = PreparationManifest.model_validate(data)
    if change == "source":
        read["parameters"]["record_id"]["source_step_id"] = another["id"]
    elif change == "field":
        read["parameters"]["record_id"]["field"] = "alternate_id"
    else:
        read["parameters"]["record_id"] = str(uuid4())
    after = PreparationManifest.model_validate(data)
    assert canonical_digest(before.model_dump(mode="json")) != canonical_digest(
        after.model_dump(mode="json")
    )


def execution_payload(preparation):
    current = datetime.now(UTC)
    return {
        "schema_version": "exercise-execution/v1",
        "preparation": preparation.model_dump(mode="json"),
        "authorization": {
            "kind": "execution",
            "execution_authorized": True,
            "approval_id": str(uuid4()),
            "reviewer": str(uuid4()),
            "manifest_digest": canonical_digest(preparation.model_dump(mode="json")),
            "issued_at": current - timedelta(seconds=1),
            "expires_at": current + timedelta(days=1),
        },
        "live_readiness": [
            {
                "configuration_id": str(preparation.configurations[0].id),
                "checked_at": current - timedelta(seconds=1),
                "expires_at": current + timedelta(days=1),
                "evidence_ids": [str(uuid4())],
            }
        ],
    }


def test_future_contract_accepts_bound_references_not_optional_or_unresolved_sources():
    data = result_manifest_data()
    preparation = PreparationManifest.model_validate(data)
    future = ExecutionManifest.model_validate(execution_payload(preparation))
    assert isinstance(
        future.preparation.draft.steps[1].parameters["record_id"], PriorResultReference
    )
    data["configurations"][0]["content"]["catalog"]["operations"][0]["results"][0]["required"] = (
        False
    )
    reseal_configuration(data)
    with pytest.raises(ValidationError, match="required output"):
        PreparationManifest.model_validate(data)
    payload = execution_payload(preparation)
    payload["preparation"] = data
    with pytest.raises(ValidationError, match="required output"):
        ExecutionManifest.model_validate(payload)
    data["draft"]["steps"][1]["parameters"]["record_id"] = None
    unresolved = PreparationManifest.model_validate(data)
    with pytest.raises(ValidationError, match="Unresolved preparation"):
        ExecutionManifest.model_validate(execution_payload(unresolved))


def test_optional_condition_outputs_and_unbound_condition_sources_are_rejected():
    data = conditional_branches(same_branch=True, explicit_dependency=True)
    root = data["draft"]["steps"][0]
    root.update(binding=None, parameters={})
    with pytest.raises(ValidationError, match="before comparing"):
        PreparationManifest.model_validate(data)
    data = conditional_branches(same_branch=True, explicit_dependency=True)
    output = data["configurations"][0]["content"]["catalog"]["operations"][1]["results"][-1]
    assert output["name"] == "quantity"
    output["required"] = False
    reseal_configuration(data)
    with pytest.raises(ValidationError, match="required output"):
        PreparationManifest.model_validate(data)
