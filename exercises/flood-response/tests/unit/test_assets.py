import csv
import hashlib
import io
import json
import os
import re
import sys
from pathlib import Path

import jsonschema
import pytest

from flood_lab.assets import ROOT, catalogs, generated
from flood_lab.openapi import artifact
from flood_lab.profile import PROFILE, seed_manifest

# Registered configuration revisions pin these exact descriptions; add versions instead.
FROZEN_OPERATIONS = {
    ("rest", "resource-request.create", "1"): (
        "73ff430ac75811f948324ec7d6896d8b4b293a701c1e84213e4e011fce2c6cb1"
    ),
    ("rest", "resource-request.read", "1"): (
        "104a563100341ee9f9e26bf18dabcd3c18b4c5df6280fddbd4ecc27968c494fb"
    ),
    ("rest", "resource-request.acknowledge", "1"): (
        "f2c2f17a49a16d4d49c7c4523464c924028507661d5799217c5faa560a19bf47"
    ),
    ("rest", "resource-request.allocate", "1"): (
        "ac02e6665a08d97674df93252a79e92e010aca7e17c41a669d1f70d460163428"
    ),
    ("sql", "shelter.occupancy.read", "2"): (
        "204985b7c56c7ae7c5cc7c0c15b578834083eeb09ddad59b286593eb012dd8b0"
    ),
    ("sql", "shelter.occupancy.update", "2"): (
        "76ea8f42638dd39ffc241cb47156990c69fc6155bda7541753e588d908b38aaf"
    ),
}
OPENAPI_TYPES = {
    "string": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "uuid": {"type": "string", "format": "uuid"},
    "datetime": {"type": "string", "format": "date-time"},
}


def openapi_value(schema):
    """Return a property's non-null schema and whether JSON null is allowed."""
    options = schema.get("anyOf", [schema])
    values = [option for option in options if option.get("type") != "null"]
    assert len(values) == 1, schema
    return values[0], len(values) != len(options)


def test_generated_assets_are_deterministic_and_checksums_match():
    first = generated()
    assert first == generated()
    manifest = json.loads(first["manifest.json"][1])
    for entry in manifest["files"]:
        assert hashlib.sha256(first[entry["path"]][1]).hexdigest() == entry["sha256"]
        assert len(first[entry["path"]][1]) == entry["size_bytes"]
    assert PROFILE["graph_sending_enabled"] is False
    assert PROFILE["maximum_total_notifications"] == 2
    assert PROFILE["maximum_escalations"] == 1
    assert PROFILE["sender"] is None
    assert PROFILE["recipients"] == []


def test_assets_use_importable_markdown_and_valid_csv():
    files = generated()
    for filename, (_, content) in files.items():
        if filename.endswith(".md"):
            narrative = content.decode()
            assert not re.search(r"^\s*\|", narrative, flags=re.MULTILINE)
            assert not re.search(r"<[A-Za-z/][^>]*>", narrative)
            assert "![" not in narrative
        if filename.endswith(".json"):
            json.loads(content)
    rows = list(csv.DictReader(io.StringIO(files["inject-schedule.csv"][1].decode())))
    assert rows[0]["occupancy"] == "108"
    assert "EXERCISE ONLY" in rows[0]["label"]
    for stage in ("initial", "escalation"):
        template = files[f"notification-{stage}.txt"][1].decode()
        assert "{{trusted_link}}" in template
        assert "Subject: EXERCISE ONLY" in template
        assert "Graph sending is absent" in template


def test_run_seed_is_deterministic_namespaced_and_not_authority():
    assert seed_manifest("test-only") == seed_manifest("test-only")
    assert seed_manifest("test-only")["run_id"] != seed_manifest("another-run")["run_id"]
    for run_key in ("", "../app", "A" * 65):
        with pytest.raises(ValueError):
            seed_manifest(run_key)
    assert seed_manifest("test-only")["synthetic"] is True


def test_catalogs_are_separate_flat_contracts():
    for kind, catalog in catalogs().items():
        assert catalog["schema_version"] == "operation-catalog/v1"
        assert set(catalog) == {"schema_version", "name", "operations"}
        for operation in catalog["operations"]:
            assert operation["invocation"]["kind"] == kind
            assert set(operation) == {
                "key",
                "version",
                "label",
                "effect",
                "invocation",
                "parameters",
                "results",
                "recovery",
            }
            for fields in (operation["parameters"], operation["results"]):
                assert len({field["name"] for field in fields}) == len(fields)
                assert all(
                    field["type"] in {"string", "integer", "number", "boolean", "uuid", "datetime"}
                    for field in fields
                )


def test_foreign_generic_catalog_schema_without_runtime_import():
    default = ROOT.parents[1] / "backend/contracts/operation-catalog-v1.schema.json"
    schema_file = Path(os.environ.get("FLOOD_LAB_CATALOG_SCHEMA", str(default)))
    if not schema_file.is_file():
        pytest.skip(
            "Foreign exported OperationCatalog schema unavailable; compatibility not verified."
        )
    schema = json.loads(schema_file.read_text())
    for catalog in catalogs().values():
        jsonschema.Draft202012Validator(schema).validate(catalog)
    assert not any(name == "gametheory" or name.startswith("gametheory.") for name in sys.modules)


def test_openapi_is_independent_authenticated_and_versioned():
    schema = json.loads(artifact())
    assert schema["info"]["version"] == "flood-lab/v1"
    assert "LabEntraBearer" in schema["components"]["securitySchemes"]
    assert schema["paths"]["/v1/runs"]["get"]["security"] == [{"LabEntraBearer": []}]
    assert not any("gametheory" in path.lower() for path in schema["paths"])


def test_rest_catalog_matches_reserved_headers_and_flat_openapi_bodies():
    schema = json.loads(artifact())
    for operation in catalogs()["rest"]["operations"]:
        invocation = operation["invocation"]
        route = schema["paths"][invocation["path"]][invocation["method"].lower()]
        parameters = {entry["name"]: entry for entry in operation["parameters"]}
        assert "idempotency_key" not in parameters
        header_parameters = {
            entry["name"]: entry for entry in route.get("parameters", []) if entry["in"] == "header"
        }
        if invocation["method"] == "POST":
            assert header_parameters["Idempotency-Key"]["required"] is True
            reference = route["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            body_schema = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]
            path_parameters = set(re.findall(r"\{([a-z_]+)\}", invocation["path"]))
            expected_json = set(parameters) - path_parameters - {"expected_version"}
            assert expected_json == set(body_schema["properties"])
        if invocation["method"] == "GET":
            path_parameters = set(re.findall(r"\{([a-z_]+)\}", invocation["path"]))
            query = {
                entry["name"]: entry
                for entry in route.get("parameters", [])
                if entry["in"] == "query"
            }
            assert set(query) == set(parameters) - path_parameters
            for name, entry in query.items():
                assert entry["required"] is parameters[name]["required"]
                assert entry["schema"]["type"] == OPENAPI_TYPES[parameters[name]["type"]]["type"]
                for bound in ("minimum", "maximum"):
                    assert entry["schema"].get(bound) == parameters[name].get(bound)
            reference = route["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
            response = schema["components"]["schemas"][reference.rsplit("/", 1)[-1]]
            for result in operation["results"]:
                value, nullable = openapi_value(response["properties"][result["name"]])
                # Serialized defaults such as contract_version are always present in responses.
                assert result["name"] in response["required"] or "default" in value
                assert nullable is not result["required"], result
                for key, expected in OPENAPI_TYPES[result["type"]].items():
                    assert value[key] == expected, result
                if "choices" in result:
                    assert result["choices"] == value.get("enum", [value.get("const")])
                if "max_length" in result:
                    assert value["maxLength"] <= result["max_length"]
        if "expected_version" in parameters:
            assert parameters["expected_version"]["type"] == "string"
            assert parameters["expected_version"]["max_length"] == 35
            assert header_parameters["If-Match"]["required"] is True
    version = schema["components"]["schemas"]["RequestView"]["properties"]["record_version"]
    assert version["pattern"] == "^v1:[0-9a-f]{32}$"


def test_registered_operation_versions_are_frozen_and_changes_are_additive():
    for kind, catalog in catalogs().items():
        for operation in catalog["operations"]:
            identity = (kind, operation["key"], operation["version"])
            if identity in FROZEN_OPERATIONS:
                encoded = json.dumps(
                    operation, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
                assert hashlib.sha256(encoded).hexdigest() == FROZEN_OPERATIONS[identity]
    keys = {
        (kind, item["key"], item["version"])
        for kind, catalog in catalogs().items()
        for item in catalog["operations"]
    }
    assert set(FROZEN_OPERATIONS) <= keys
    assert ("rest", "resource-request.milestones", "1") in keys


def test_milestone_read_declares_flat_authoritative_timing_results():
    operation = next(
        item
        for item in catalogs()["rest"]["operations"]
        if item["key"] == "resource-request.milestones"
    )
    assert operation["effect"] == "read"
    assert operation["invocation"] == {
        "kind": "rest",
        "method": "GET",
        "path": "/v1/runs/{run_id}/requests/{request_id}/milestones",
    }
    parameters = {field["name"]: field for field in operation["parameters"]}
    assert set(parameters) == {
        "run_id",
        "request_id",
        "acknowledge_within_seconds",
        "allocate_within_seconds",
    }
    for name in ("acknowledge_within_seconds", "allocate_within_seconds"):
        assert parameters[name] == {
            "name": name,
            "type": "integer",
            "required": True,
            "minimum": 1,
            "maximum": 604800,
        }
    results = {field["name"]: (field["type"], field["required"]) for field in operation["results"]}
    assert results == {
        "contract_version": ("string", True),
        "request_id": ("uuid", True),
        "run_id": ("uuid", True),
        "record_version": ("string", True),
        "status": ("string", True),
        "quantity_requested": ("integer", True),
        "quantity_allocated": ("integer", True),
        "created_at": ("datetime", True),
        "created_event_id": ("uuid", False),
        "acknowledged": ("boolean", True),
        "acknowledged_at": ("datetime", False),
        "acknowledgement_event_id": ("uuid", False),
        "allocated_total_by_deadline": ("integer", True),
        "allocation_completed_at": ("datetime", False),
        "allocation_completed_event_id": ("uuid", False),
        "allocation_event_count": ("integer", True),
        "acknowledgement_deadline": ("datetime", True),
        "allocation_deadline": ("datetime", True),
        "as_of": ("datetime", True),
        "acknowledged_on_time": ("boolean", False),
        "allocated_on_time": ("boolean", False),
        "acknowledgement_reason": ("string", True),
        "allocation_reason": ("string", True),
    }
    assert "absence alone is not lateness" in operation["recovery"]


def test_sql_percentage_is_bindable_and_read_evidence_is_optional():
    operations = {operation["key"]: operation for operation in catalogs()["sql"]["operations"]}
    read = operations["shelter.occupancy.read"]
    update = operations["shelter.occupancy.update"]
    assert read["version"] == update["version"] == "2"
    fields = {field["name"]: field for field in read["results"]}
    assert fields["occupancy_percent"] == {
        "name": "occupancy_percent",
        "type": "number",
        "required": True,
        "minimum": 0,
        "maximum": 100,
    }
    assert fields["durable_event_id"]["required"] is False
    assert fields["committed_at"]["required"] is False
    mutation_fields = {field["name"]: field for field in update["results"]}
    assert mutation_fields["occupancy_percent"]["type"] == "number"
    assert mutation_fields["occupancy_percent"]["required"] is False  # Legacy immutable receipts.
    assert mutation_fields["durable_event_id"]["required"] is True
    guide = generated()["operator-and-ui-setup.md"][1].decode()
    assert "occupancy_percent" in guide
    assert "literal 85" in guide
    assert "Missing evidence makes timing indeterminate" in guide
    assert "create-to-observe" in guide
    assert "source_step_id" in guide
    assert "rather than guessing" in guide
    assert "mutually exclusive branch sibling is not a valid source" in guide
