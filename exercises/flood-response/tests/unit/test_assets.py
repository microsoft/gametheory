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
        if "expected_version" in parameters:
            assert parameters["expected_version"]["type"] == "string"
            assert parameters["expected_version"]["max_length"] == 35
            assert header_parameters["If-Match"]["required"] is True
    version = schema["components"]["schemas"]["RequestView"]["properties"]["record_version"]
    assert version["pattern"] == "^v1:[0-9a-f]{32}$"


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
