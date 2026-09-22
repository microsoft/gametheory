import copy
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from gametheory import cli
from gametheory.domain import ScenarioContent
from gametheory.preparation import (
    AssetPin,
    BoardDraft,
    ConfigurationSnapshot,
    ConnectionConfiguration,
    ExecutionManifest,
    OperationCatalog,
    OperationField,
    PreparationApprovalInput,
    PreparationManifest,
    ScenarioPin,
    canonical_digest,
    canonical_json,
    preparation_findings,
    strict_json,
    validate_configuration_kind,
    validate_scalar,
)


def catalog_data():
    return {
        "schema_version": "operation-catalog/v1",
        "name": "Fictional record registry",
        "operations": [
            {
                "key": "record.read",
                "version": "1",
                "label": "Inspect an owned record",
                "effect": "read",
                "invocation": {"kind": "rest", "method": "GET", "path": "/records/{record_id}"},
                "parameters": [{"name": "record_id", "type": "uuid"}],
                "results": [{"name": "quantity", "type": "integer", "minimum": 0}],
                "recovery": "Read-only; no target changes.",
            }
        ],
    }


def manifest_data():
    wid, bid, config_id = uuid4(), uuid4(), uuid4()
    content = ConnectionConfiguration(
        classification="nonproduction",
        resource_id="registry-example",
        endpoint="https://records.example.invalid",
        identity_ref="identity/reader",
        catalog=OperationCatalog.model_validate(catalog_data()),
    )
    config = ConfigurationSnapshot(
        id=config_id,
        workspace_id=wid,
        connection_id=uuid4(),
        version=1,
        content=content,
        digest=canonical_digest(content.model_dump(mode="json")),
        connection_kind="rest",
        connection_name="Registered service",
        environment_id=uuid4(),
        environment_name="Test",
        created_by=uuid4(),
        created_at=datetime.now(UTC),
    )
    start = datetime.now(UTC) + timedelta(hours=1)
    return {
        "board_id": str(bid),
        "workspace_id": str(wid),
        "board_version": 1,
        "draft": {
            "name": "Record readiness exercise",
            "steps": [
                {
                    "id": str(uuid4()),
                    "label": "Inspect record",
                    "kind": "operation",
                    "binding": {
                        "configuration_id": str(config_id),
                        "operation_key": "record.read",
                        "operation_version": "1",
                    },
                    "parameters": {"record_id": str(uuid4())},
                }
            ],
            "window": {
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(hours=1)).isoformat(),
            },
            "recovery": "Read-only; retain review evidence.",
        },
        "scenario": {
            "scenario_id": str(uuid4()),
            "revision_version": 3,
            "content": ScenarioContent(title="Published record exercise").model_dump(mode="json"),
        },
        "assets": [],
        "configurations": [config.model_dump(mode="json")],
    }


def update_config(data, **changes):
    configuration = data["configurations"][0]
    configuration["content"].update(changes)
    configuration["digest"] = canonical_digest(configuration["content"])


def test_restricted_operation_catalog_and_canonical_digest():
    catalog = OperationCatalog.model_validate(catalog_data())
    data = catalog.model_dump(mode="json")
    shuffled = dict(reversed(list(data.items())))
    assert canonical_digest(data) == canonical_digest(shuffled)
    assert len(canonical_digest(data)) == 64
    assert " " not in canonical_json({"b": 1, "a": ["é", True]})
    assert canonical_json({"b": 1, "a": ["é", True]}) == '{"a":["é",true],"b":1}'
    assert canonical_digest([1, 2]) != canonical_digest([2, 1])
    assert OperationCatalog.model_validate(strict_json(json.dumps(data))) == catalog


@pytest.mark.parametrize(
    "raw",
    [
        '{"name":"first","name":"second"}',
        '{"operations":[{"key":"one","key":"two"}]}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":-Infinity}',
        '{"value":1e999}',
        '{"value":"\\ud800"}',
    ],
)
def test_duplicate_keys_and_nonfinite_json_rejected(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


@pytest.mark.parametrize(
    "path",
    [
        "https://example.invalid/path",
        "//example.invalid/path",
        "/path?query=1",
        "/path#fragment",
        "/../path",
        "/a/./b",
        "/a/%2e%2e/b",
        "/a\\b",
        "/{record_id}/suffix{record_id}",
        "/{unknown}",
        "/a//b",
        "/a\nb",
        "/a;command",
    ],
)
def test_rest_contract_cannot_describe_arbitrary_requests(path):
    data = catalog_data()
    data["operations"][0]["invocation"]["path"] = path
    with pytest.raises(ValidationError):
        OperationCatalog.model_validate(data)


@pytest.mark.parametrize(
    "invocation",
    [
        {"kind": "sql", "procedure": "dbo.read_record; DROP TABLE records"},
        {"kind": "sql", "procedure": "database.dbo.read_record"},
        {"kind": "sql", "procedure": "dbo.[read_record]"},
        {"kind": "sql", "procedure": "dbo.read_record", "sql": "SELECT 1"},
        {
            "kind": "rest",
            "method": "GET",
            "path": "/records/{record_id}",
            "headers": {"x-key": "not-allowed"},
        },
        {"kind": "rest", "method": "TRACE", "path": "/records/{record_id}"},
        {"kind": "mcp", "tool": "any"},
    ],
)
def test_unsupported_transport_instructions_rejected(invocation):
    data = catalog_data()
    data["operations"][0]["invocation"] = invocation
    with pytest.raises(ValidationError):
        OperationCatalog.model_validate(data)


@pytest.mark.parametrize(
    "mutation",
    ["duplicate_operation", "duplicate_field", "remote_ref", "secret", "bad_type", "optional_path"],
)
def test_invalid_catalog_fields_rejected(mutation):
    data = catalog_data()
    operation = data["operations"][0]
    if mutation == "duplicate_operation":
        data["operations"].append(copy.deepcopy(operation))
    elif mutation == "duplicate_field":
        operation["parameters"].append(copy.deepcopy(operation["parameters"][0]))
    elif mutation == "remote_ref":
        operation["parameters"][0]["$ref"] = "https://example.invalid/schema"
    elif mutation == "secret":
        operation["parameters"].append({"name": "access_token", "type": "string"})
    elif mutation == "bad_type":
        operation["results"][0]["type"] = "object"
    elif mutation == "optional_path":
        operation["parameters"][0]["required"] = False
    with pytest.raises(ValidationError):
        OperationCatalog.model_validate(data)


@pytest.mark.parametrize(
    "field",
    [
        {"name": "expected_version", "type": "integer"},
        {"name": "expected_version", "type": "string"},
        {"name": "expected_version", "type": "string", "max_length": 64, "choices": ['"v1"']},
        {"name": "idempotency_key", "type": "string", "max_length": 128},
        {"name": "idempotencyKey", "type": "string", "max_length": 128},
        {"name": "if_match", "type": "string", "max_length": 64},
    ],
)
def test_rest_controls_cannot_be_arbitrary_caller_selected_headers(field):
    data = catalog_data()
    data["operations"][0]["parameters"].append(field)
    with pytest.raises(ValidationError):
        OperationCatalog.model_validate(data)


def test_expected_version_is_reserved_for_rest_headers_not_path_substitution():
    data = catalog_data()
    operation = data["operations"][0]
    operation["parameters"].append(
        {"name": "expected_version", "type": "string", "required": True, "max_length": 64}
    )
    OperationCatalog.model_validate(data)
    operation["invocation"]["path"] += "/{expected_version}"
    with pytest.raises(ValidationError, match="header control"):
        OperationCatalog.model_validate(data)
    operation["invocation"] = {"kind": "sql", "procedure": "registry.read_record"}
    operation["parameters"][-1] = {"name": "expected_version", "type": "integer", "minimum": 1}
    OperationCatalog.model_validate(data)


@pytest.mark.parametrize(
    "version",
    ['"v1"', 'W/"v1"', "*", "", "two words", "v1\r\nOther: value", "v1\x7f", "version-😀", 1],
)
def test_expected_version_bindings_must_be_bare_bounded_opaque_values(version):
    data = manifest_data()
    config = data["configurations"][0]
    config["content"]["catalog"]["operations"][0]["parameters"].append(
        {"name": "expected_version", "type": "string", "required": True, "max_length": 64}
    )
    normalized = ConnectionConfiguration.model_validate(config["content"]).model_dump(mode="json")
    config["content"] = normalized
    config["digest"] = canonical_digest(normalized)
    data["draft"]["steps"][0]["parameters"]["expected_version"] = version
    with pytest.raises(ValidationError):
        PreparationManifest.model_validate(data)


@pytest.mark.parametrize("version", ["record-v1", "AAAAAAAAB+8=", "v1/subversion", None])
def test_expected_version_accepts_opaque_or_explicitly_unresolved_input(version):
    data = manifest_data()
    config = data["configurations"][0]
    config["content"]["catalog"]["operations"][0]["parameters"].append(
        {"name": "expected_version", "type": "string", "required": True, "max_length": 64}
    )
    normalized = ConnectionConfiguration.model_validate(config["content"]).model_dump(mode="json")
    config["content"] = normalized
    config["digest"] = canonical_digest(normalized)
    data["draft"]["steps"][0]["parameters"]["expected_version"] = version
    manifest = PreparationManifest.model_validate(data)
    missing_version = [
        finding
        for finding in preparation_findings(manifest)
        if finding.code == "missing_parameter" and finding.path.endswith("/expected_version")
    ]
    assert bool(missing_version) is (version is None)
    assert manifest.execution_authorized is False


@pytest.mark.parametrize(
    "field_type,value",
    [
        ("integer", "7"),
        ("integer", 7.0),
        ("integer", True),
        ("number", "7.5"),
        ("number", False),
        ("number", float("nan")),
        ("number", float("inf")),
        ("boolean", 1),
        ("boolean", "true"),
        ("string", 1),
        ("uuid", "missing"),
        ("uuid", 1),
        ("datetime", "2026-01-01T12:00:00"),
        ("datetime", "2026-01-01"),
        ("datetime", 123),
    ],
)
def test_parameter_types_do_not_coerce(field_type, value):
    with pytest.raises((ValueError, TypeError)):
        validate_scalar(OperationField(name="value", type=field_type), value)


@pytest.mark.parametrize(
    "field_type,value",
    [
        ("integer", 7),
        ("number", 7.5),
        ("number", 7),
        ("boolean", True),
        ("string", "7"),
        ("uuid", str(uuid4())),
        ("datetime", "2026-01-01T12:00:00+02:00"),
        ("datetime", "2026-01-01T12:00:00Z"),
    ],
)
def test_supported_scalar_parameters(field_type, value):
    validate_scalar(OperationField(name="value", type=field_type), value)


def test_constraints_are_typed_and_enforced():
    numeric = OperationField(name="count", type="integer", minimum=0, maximum=20)
    validate_scalar(numeric, 20)
    for bad in (-1, 21):
        with pytest.raises(ValueError):
            validate_scalar(numeric, bad)
    text = OperationField(name="state", type="string", max_length=5, choices=["open", "done"])
    validate_scalar(text, "open")
    with pytest.raises(ValueError):
        validate_scalar(text, "other")
    for constraint in (
        {"type": "uuid", "max_length": 20},
        {"type": "string", "minimum": 0},
        {"type": "number", "minimum": 5, "maximum": 1},
        {"type": "number", "minimum": "1"},
        {"type": "boolean", "required": "true"},
        {"type": "string", "choices": ["a", "a"]},
    ):
        with pytest.raises(ValidationError):
            OperationField(name="field", **constraint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.invalid",
        "https://user:password@example.invalid",
        "https://example.invalid?credential=value",
        "https://example.invalid#fragment",
        "https://example.invalid/a/../b",
        "https://example.invalid/%2e",
        "https://example.invalid:invalid",
        "https://example.invalid\\bad",
    ],
)
def test_target_metadata_does_not_accept_credentials_or_arbitrary_urls(endpoint):
    with pytest.raises(ValueError):
        content = ConnectionConfiguration(
            catalog=OperationCatalog.model_validate(catalog_data()), endpoint=endpoint
        )
        validate_configuration_kind(content, "rest")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.invalid",
        "https://example.invalid/base/v1",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
        "http://[::1]:8080",
        "",
    ],
)
def test_safe_target_metadata_and_unresolved_values(endpoint):
    content = ConnectionConfiguration(
        catalog=OperationCatalog.model_validate(catalog_data()), endpoint=endpoint
    )
    validate_configuration_kind(content, "rest")


def test_connection_kind_and_graph_routing_are_explicit():
    content = ConnectionConfiguration(catalog=OperationCatalog.model_validate(catalog_data()))
    for kind in ("sql", "graph", "mcp"):
        with pytest.raises(ValueError):
            validate_configuration_kind(content, kind)
    data = catalog_data()
    operation = data["operations"][0]
    operation.update(effect="notify", invocation={"kind": "graph", "template_key": "fixed-notice"})
    OperationCatalog.model_validate(data)
    for name in (
        "sender",
        "recipients",
        "to",
        "cc",
        "bcc",
        "attachments",
        "body",
        "subject",
        "reply_to",
        "toRecipients",
        "bcc_recipients",
        "message_body",
        "sender_email",
        "attachment_ids",
    ):
        operation["parameters"] = [{"name": name, "type": "string"}]
        with pytest.raises(ValidationError):
            OperationCatalog.model_validate(data)


def condition_draft():
    read, branch, yes, no = [str(uuid4()) for _ in range(4)]
    return {
        "name": "Independent proposed flow",
        "steps": [
            {"id": read, "label": "Read", "kind": "operation"},
            {
                "id": branch,
                "label": "Compare",
                "kind": "condition",
                "depends_on": [read],
                "condition": {
                    "source_step_id": read,
                    "result_field": "quantity",
                    "operator": "gte",
                    "value": 5,
                    "if_true": [yes],
                    "if_false": [no],
                },
            },
            {"id": yes, "label": "Bounded wait", "kind": "wait", "wait_seconds": 60},
            {"id": no, "label": "Review", "kind": "operation"},
        ],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "cycle",
        "branch_cycle",
        "missing_dependency",
        "duplicate_dependency",
        "overlap_branch",
        "missing_branch",
        "future_result",
        "unknown_result_source",
        "unsupported_kind",
        "unbounded_wait",
        "wrong_kind_fields",
        "duplicate_id",
    ],
)
def test_separate_preparation_flow_rejects_invalid_graph(mutation):
    data = condition_draft()
    read, branch, yes, no = data["steps"]
    if mutation == "cycle":
        read["depends_on"] = [yes["id"]]
    elif mutation == "branch_cycle":
        branch["condition"]["if_true"] = [read["id"]]
    elif mutation == "missing_dependency":
        read["depends_on"] = [str(uuid4())]
    elif mutation == "duplicate_dependency":
        branch["depends_on"] *= 2
    elif mutation == "overlap_branch":
        branch["condition"]["if_false"] = branch["condition"]["if_true"][:]
    elif mutation == "missing_branch":
        branch["condition"]["if_true"] = [str(uuid4())]
    elif mutation == "future_result":
        branch["condition"]["source_step_id"] = no["id"]
    elif mutation == "unknown_result_source":
        branch["condition"]["source_step_id"] = str(uuid4())
    elif mutation == "unsupported_kind":
        no["kind"] = "script"
    elif mutation == "unbounded_wait":
        yes["wait_seconds"] = 86401
    elif mutation == "wrong_kind_fields":
        no["wait_seconds"] = 30
    elif mutation == "duplicate_id":
        no["id"] = read["id"]
    with pytest.raises(ValidationError):
        BoardDraft.model_validate(data)


def test_valid_branch_and_bounded_waits_are_not_authoring_graph_execution():
    draft = BoardDraft.model_validate(condition_draft())
    assert [step.kind for step in draft.steps] == ["operation", "condition", "wait", "operation"]
    assert all(step.authoring_node_id is None for step in draft.steps)
    data = condition_draft()
    data["steps"] += [
        {"id": str(uuid4()), "label": "Wait", "kind": "wait", "wait_seconds": 86400}
        for _ in range(8)
    ]
    with pytest.raises(ValidationError, match="seven days"):
        BoardDraft.model_validate(data)


def test_exact_operation_binding_result_types_and_authoring_pins():
    data = manifest_data()
    original = PreparationManifest.model_validate(data)
    assert original.draft.steps[0].parameters
    for mutation in (
        "operation_version",
        "parameter",
        "unknown_parameter",
        "authoring_node",
        "workspace",
    ):
        altered = copy.deepcopy(data)
        step = altered["draft"]["steps"][0]
        if mutation == "operation_version":
            step["binding"]["operation_version"] = "2"
        elif mutation == "parameter":
            step["parameters"]["record_id"] = 42
        elif mutation == "unknown_parameter":
            step["parameters"]["extra"] = "unknown"
        elif mutation == "authoring_node":
            step["authoring_node_id"] = str(uuid4())
        else:
            altered["configurations"][0]["workspace_id"] = str(uuid4())
        with pytest.raises(ValidationError):
            PreparationManifest.model_validate(altered)
    steps = data["draft"]["steps"]
    end = {"id": str(uuid4()), "kind": "wait", "label": "Observe", "wait_seconds": 30}
    comparison = {
        "id": str(uuid4()),
        "kind": "condition",
        "label": "Compare",
        "depends_on": [steps[0]["id"]],
        "condition": {
            "source_step_id": steps[0]["id"],
            "result_field": "quantity",
            "operator": "gte",
            "value": 5,
            "if_true": [end["id"]],
        },
    }
    steps.extend([comparison, end])
    PreparationManifest.model_validate(data)
    for key, value in (("result_field", "undeclared"), ("value", "5")):
        invalid = copy.deepcopy(data)
        invalid["draft"]["steps"][1]["condition"][key] = value
        with pytest.raises(ValidationError):
            PreparationManifest.model_validate(invalid)
    optional = copy.deepcopy(data)
    config = optional["configurations"][0]
    config["content"]["catalog"]["operations"][0]["results"][0]["required"] = False
    config["digest"] = canonical_digest(config["content"])
    with pytest.raises(ValidationError, match="required output"):
        PreparationManifest.model_validate(optional)


def test_notification_policy_and_template_versions_are_pinned_not_sent():
    data = manifest_data()
    config = data["configurations"][0]
    config["connection_kind"] = "graph"
    operation = config["content"]["catalog"]["operations"][0]
    operation["effect"] = "notify"
    operation["invocation"] = {"kind": "graph", "template_key": "fixed-notice"}
    config["content"]["notification"] = {
        "template_asset_id": None,
        "sender": "",
        "recipients": [],
        "trusted_link": "",
    }
    config["digest"] = canonical_digest(config["content"])
    unresolved = PreparationManifest.model_validate(data)
    findings = {finding.code for finding in preparation_findings(unresolved)}
    assert {
        "missing_notification_template_asset_id",
        "missing_notification_sender",
        "missing_notification_recipients",
        "missing_notification_trusted_link",
        "notification_budget_exceeded",
        "delivery_unverified",
        "execution_disabled",
    } <= findings
    asset = AssetPin(
        id=uuid4(), name="notice.md", media_type="text/markdown", sha256="a" * 64, size=10
    )
    config["template_asset"] = asset.model_dump(mode="json")
    config["content"]["notification"] = {
        "template_asset_id": str(asset.id),
        "sender": "operator@example.invalid",
        "recipients": ["reviewer@example.invalid"],
        "trusted_link": "https://records.example.invalid/review",
    }
    config["digest"] = canonical_digest(config["content"])
    data["draft"]["notification_budget"] = 1
    complete = PreparationManifest.model_validate(data)
    codes = {finding.code for finding in preparation_findings(complete)}
    assert "delivery_unverified" in codes
    assert "missing_notification_template_asset_id" not in codes
    assert complete.execution_authorized is False
    assert canonical_digest(complete.model_dump(mode="json")) != canonical_digest(
        unresolved.model_dump(mode="json")
    )
    config["content"]["notification"]["template_asset_id"] = str(uuid4())
    config["digest"] = canonical_digest(config["content"])
    with pytest.raises(ValidationError, match="template asset"):
        PreparationManifest.model_validate(data)


@pytest.mark.parametrize(
    "notification",
    [
        {"sender": "Name <sender@example.invalid>"},
        {"sender": "sender@example.invalid\r\nBcc: other@example.invalid"},
        {"recipients": ["reviewer@example.invalid", "REVIEWER@example.invalid"]},
        {"recipients": ["first@example.invalid,second@example.invalid"]},
        {"trusted_link": "https://user:pass@example.invalid"},
    ],
)
def test_notification_configuration_rejects_implicit_routing(notification):
    with pytest.raises(ValidationError):
        ConnectionConfiguration(
            catalog=OperationCatalog(name="Fixed notices", operations=[]),
            notification=notification,
        )


def test_unresolved_preparation_has_honest_findings_and_never_execution():
    data = manifest_data()
    data["draft"]["steps"][0]["parameters"]["record_id"] = None
    data["draft"].update(window=None, recovery="")
    update_config(data, classification="unknown", resource_id="", endpoint="", identity_ref="")
    manifest = PreparationManifest.model_validate(data)
    codes = {finding.code for finding in preparation_findings(manifest)}
    assert {
        "missing_parameter",
        "missing_window",
        "missing_recovery",
        "missing_target_resource_id",
        "missing_target_endpoint",
        "missing_target_identity_ref",
        "target_classification_ineligible",
        "target_contents_unverified",
        "connectivity_unverified",
        "permissions_unverified",
        "live_readiness_unverified",
        "execution_disabled",
    } <= codes
    assert manifest.execution_authorized is False
    with pytest.raises(ValidationError):
        ExecutionManifest.model_validate(manifest.model_dump(mode="json"))


@pytest.mark.parametrize(
    "classification,environment",
    [
        ("unknown", "Test"),
        ("production", "Test"),
        ("nonproduction", "Production"),
        ("nonproduction", " production "),
    ],
)
def test_environment_label_cannot_authorize_production(classification, environment):
    data = manifest_data()
    update_config(data, classification=classification)
    data["configurations"][0]["environment_name"] = environment
    assert "target_classification_ineligible" in {
        finding.code for finding in preparation_findings(PreparationManifest.model_validate(data))
    }


@pytest.mark.parametrize(
    "change",
    [
        "name",
        "parameter",
        "window",
        "budget",
        "recovery",
        "configuration",
        "scenario",
        "asset",
        "version",
    ],
)
def test_every_material_manifest_input_changes_digest(change):
    data = manifest_data()
    asset_id = uuid4()
    data["scenario"]["content"]["asset_ids"] = [str(asset_id)]
    data["assets"] = [
        AssetPin(
            id=asset_id, name="brief.txt", media_type="text/plain", sha256="a" * 64, size=10
        ).model_dump(mode="json")
    ]
    original = PreparationManifest.model_validate(data)
    altered = copy.deepcopy(data)
    if change == "name":
        altered["draft"]["name"] = "Changed board"
    elif change == "parameter":
        altered["draft"]["steps"][0]["parameters"]["record_id"] = str(uuid4())
    elif change == "window":
        altered["draft"]["window"]["ends_at"] = (
            datetime.fromisoformat(altered["draft"]["window"]["ends_at"]) + timedelta(minutes=5)
        ).isoformat()
    elif change == "budget":
        altered["draft"]["notification_budget"] = 2
    elif change == "recovery":
        altered["draft"]["recovery"] = "Revised explicit recovery policy"
    elif change == "configuration":
        update_config(altered, endpoint="https://other.example.invalid")
    elif change == "scenario":
        altered["scenario"]["content"]["title"] = "Revised pinned content"
    elif change == "asset":
        altered["assets"][0]["sha256"] = "b" * 64
    else:
        altered["board_version"] += 1
    changed = PreparationManifest.model_validate(altered)
    assert canonical_digest(original.model_dump(mode="json")) != canonical_digest(
        changed.model_dump(mode="json")
    )
    assert original.model_dump(mode="json") != changed.model_dump(mode="json")


def test_asset_versions_and_configuration_content_cannot_be_substituted():
    data = manifest_data()
    data["scenario"]["content"]["asset_ids"] = [str(uuid4())]
    with pytest.raises(ValidationError, match="exact ordered"):
        PreparationManifest.model_validate(data)
    data = manifest_data()
    data["configurations"][0]["content"]["endpoint"] = "https://changed.example.invalid"
    with pytest.raises(ValidationError, match="digest"):
        PreparationManifest.model_validate(data)


@pytest.mark.parametrize("value", [False, 0, 1, "true", None])
def test_approval_requires_literal_acknowledgement(value):
    with pytest.raises(ValidationError):
        PreparationApprovalInput(
            preview_id=uuid4(),
            digest="a" * 64,
            decision="approved",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            acknowledge_unverified=value,
        )


@pytest.mark.parametrize("expires_at", ["2026-01-01T12:00:00", "2026-01-01", 1800000000, None])
def test_approval_expiry_requires_explicit_timezone(expires_at):
    with pytest.raises(ValidationError):
        PreparationApprovalInput(
            preview_id=uuid4(),
            digest="a" * 64,
            decision="approved",
            expires_at=expires_at,
            acknowledge_unverified=True,
        )


def test_distinct_future_execution_contract_requires_fresh_complete_authority():
    preparation = PreparationManifest.model_validate(manifest_data())
    future = {
        "schema_version": "exercise-execution/v1",
        "preparation": preparation.model_dump(mode="json"),
        "authorization": {
            "kind": "execution",
            "execution_authorized": True,
            "approval_id": str(uuid4()),
            "reviewer": str(uuid4()),
            "manifest_digest": canonical_digest(preparation.model_dump(mode="json")),
            "issued_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
        "live_readiness": [
            {
                "configuration_id": str(preparation.configurations[0].id),
                "checked_at": datetime.now(UTC).isoformat(),
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "evidence_ids": [str(uuid4())],
            }
        ],
    }
    ExecutionManifest.model_validate(future)
    for mutation in (
        "preparation_authority",
        "unresolved",
        "no_live_evidence",
        "expired",
        "unknown",
        "wrong_digest",
        "fake_true",
    ):
        invalid = copy.deepcopy(future)
        if mutation == "preparation_authority":
            invalid["authorization"].update(kind="preparation", execution_authorized=False)
        elif mutation == "unresolved":
            invalid["preparation"]["draft"]["steps"][0]["parameters"]["record_id"] = None
            invalid["authorization"]["manifest_digest"] = canonical_digest(invalid["preparation"])
        elif mutation == "no_live_evidence":
            invalid["live_readiness"] = []
        elif mutation == "expired":
            invalid["authorization"]["expires_at"] = (
                datetime.now(UTC) - timedelta(seconds=1)
            ).isoformat()
        elif mutation == "unknown":
            invalid["authorization"]["secret"] = "not-accepted"
        elif mutation == "wrong_digest":
            invalid["authorization"]["manifest_digest"] = "b" * 64
        else:
            invalid["authorization"]["execution_authorized"] = 1
        with pytest.raises(ValidationError):
            ExecutionManifest.model_validate(invalid)


def test_exported_contract_schemas_match_pure_types():
    directory = Path(__file__).parents[1] / "contracts"
    assert (
        json.loads((directory.parent / "operation-catalog.schema.json").read_text())
        == OperationCatalog.model_json_schema()
    )
    for filename, model in (
        ("operation-catalog-v1.schema.json", OperationCatalog),
        ("connection-configuration-v1.schema.json", ConnectionConfiguration),
        ("preparation-manifest-v1.schema.json", PreparationManifest),
        ("execution-manifest-v1.schema.json", ExecutionManifest),
    ):
        assert json.loads((directory / filename).read_text()) == model.model_json_schema()
    assert (
        ScenarioPin(
            scenario_id=uuid4(),
            revision_version=1,
            content=ScenarioContent(title="Empty publication"),
        ).content.nodes
        == []
    )


def test_standalone_catalog_schema_cli_is_repeatable_without_services(monkeypatch):
    output = MagicMock()
    path = MagicMock(return_value=output)
    database = MagicMock(side_effect=AssertionError("Schema generation cannot open SQL"))
    monkeypatch.setattr(cli, "Path", path)
    monkeypatch.setattr(cli, "session_factory", database)
    monkeypatch.setattr(sys, "argv", ["gametheory", "operation-catalog-schema"])
    cli.main()
    path.assert_called_once_with("backend/operation-catalog.schema.json")
    assert json.loads(output.write_text.call_args.args[0]) == OperationCatalog.model_json_schema()
    database.assert_not_called()
