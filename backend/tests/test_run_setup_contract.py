"""Golden run-setup contract shared with the web form tests.

The web tests build RunCreate bindings from this pinned preview through the guided form model
and must produce exactly ``run_create``. This test proves the same bindings are executable under
exercise-execution/v2. Regenerate with ``GT_UPDATE_GOLDEN=1 pytest backend/tests/test_run_setup_contract.py``.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from gametheory.domain import Objective, ScenarioContent
from gametheory.execution import (
    ObjectiveRule,
    Observation,
    RecoveryBinding,
    RunManifest,
    execution_issues,
)
from gametheory.preparation import (
    BoardDraft,
    ConfigurationSnapshot,
    ConnectionConfiguration,
    PreparationManifest,
    PreparationStep,
    PreparationWindow,
    ScenarioPin,
    canonical_digest,
)

GOLDEN = Path(__file__).parent / "fixtures" / "guided-run-setup.json"
IDS = {
    name: UUID(f"32000000-0000-4000-8000-{index:012x}")
    for index, name in enumerate(
        [
            "workspace",
            "board",
            "scenario",
            "sql_connection",
            "rest_connection",
            "sql_configuration",
            "rest_configuration",
            "environment",
            "author",
            "raise",
            "read_occupancy",
            "open_ticket",
            "read_ticket",
            "breach",
            "acknowledged",
            "shelter",
        ],
        start=1,
    )
}
CREATED = datetime(2026, 9, 22, 12, tzinfo=UTC)
RECEIPT = [
    {"name": "outcome", "type": "string"},
    {"name": "durable_event_id", "type": "uuid"},
    {"name": "committed_at", "type": "datetime"},
]


def reference(step, field):
    return {"source_step_id": str(IDS[step]), "field": field}


def snapshot(kind, catalog):
    content = ConnectionConfiguration.model_validate(
        {
            "classification": "nonproduction",
            "resource_id": f"fixture/{kind}",
            "endpoint": "sql.fixture.invalid"
            if kind == "sql"
            else "https://tickets.fixture.invalid",
            "database": "exercise_fixture" if kind == "sql" else "",
            "identity_ref": f"fixture/{kind}/executor",
            "catalog": catalog,
        }
    )
    return ConfigurationSnapshot(
        id=IDS[f"{kind}_configuration"],
        workspace_id=IDS["workspace"],
        connection_id=IDS[f"{kind}_connection"],
        version=1,
        content=content,
        digest=canonical_digest(content.model_dump(mode="json")),
        connection_kind=kind,
        connection_name="Capacity database" if kind == "sql" else "Resource ticket API",
        environment_id=IDS["environment"],
        environment_name="Training lab",
        created_by=IDS["author"],
        created_at=CREATED,
    )


def golden_document():
    sql = snapshot(
        "sql",
        {
            "name": "Capacity records — golden fixture",
            "operations": [
                {
                    "key": "capacity.update",
                    "version": "2",
                    "label": "Set shelter occupancy",
                    "effect": "write",
                    "invocation": {"kind": "sql", "procedure": "fixture.UpdateOccupancy"},
                    "parameters": [
                        {"name": "shelter_id", "type": "uuid"},
                        {"name": "occupancy", "type": "integer", "minimum": 0, "maximum": 500},
                        {"name": "idempotency_key", "type": "string", "max_length": 128},
                    ],
                    "results": [
                        {"name": "occupancy_percent", "type": "number"},
                        {"name": "record_version", "type": "string", "max_length": 128},
                        *RECEIPT,
                    ],
                    "recovery": "Restore seeded occupancy only if the version still matches.",
                },
                {
                    "key": "capacity.read",
                    "version": "2",
                    "label": "Read shelter occupancy",
                    "effect": "read",
                    "invocation": {"kind": "sql", "procedure": "fixture.ReadOccupancy"},
                    "parameters": [{"name": "shelter_id", "type": "uuid"}],
                    "results": [
                        {"name": "occupancy_percent", "type": "number"},
                        {"name": "committed_at", "type": "datetime", "required": False},
                    ],
                    "recovery": "Read-only; no target mutation.",
                },
            ],
        },
    )
    rest = snapshot(
        "rest",
        {
            "name": "Resource tickets — golden fixture",
            "operations": [
                {
                    "key": "ticket.create",
                    "version": "1",
                    "label": "Open a resource ticket",
                    "effect": "write",
                    "invocation": {"kind": "rest", "method": "POST", "path": "/tickets"},
                    "parameters": [{"name": "title", "type": "string", "max_length": 80}],
                    "results": [
                        {"name": "record_id", "type": "uuid"},
                        {"name": "run_id", "type": "uuid"},
                        {"name": "record_version", "type": "string", "max_length": 128},
                        {"name": "created_at", "type": "datetime"},
                        *RECEIPT,
                    ],
                    "recovery": "Close the ticket only while unchanged and owned by this run.",
                },
                {
                    "key": "ticket.read",
                    "version": "1",
                    "label": "Read a resource ticket",
                    "effect": "read",
                    "invocation": {"kind": "rest", "method": "GET", "path": "/tickets/{record_id}"},
                    "parameters": [{"name": "record_id", "type": "uuid"}],
                    "results": [
                        {"name": "acknowledged", "type": "boolean"},
                        {"name": "acknowledged_at", "type": "datetime", "required": False},
                        {"name": "record_version", "type": "string", "max_length": 128},
                    ],
                    "recovery": "Read-only; no target mutation.",
                },
                {
                    "key": "ticket.close",
                    "version": "1",
                    "label": "Close an exercise ticket",
                    "effect": "write",
                    "invocation": {
                        "kind": "rest",
                        "method": "POST",
                        "path": "/tickets/{record_id}/close",
                    },
                    "parameters": [
                        {"name": "record_id", "type": "uuid"},
                        {"name": "run_id", "type": "uuid"},
                        {"name": "expected_version", "type": "string", "max_length": 128},
                    ],
                    "results": [
                        {"name": "record_version", "type": "string", "max_length": 128},
                        *RECEIPT,
                    ],
                    "recovery": "Closing is itself the recovery; human edits are preserved.",
                },
            ],
        },
    )

    def step(name, label, kind, key, version, parameters, depends_on=()):
        return PreparationStep.model_validate(
            {
                "id": IDS[name],
                "label": label,
                "kind": "operation",
                "depends_on": [IDS[item] for item in depends_on],
                "binding": {
                    "configuration_id": IDS[f"{kind}_configuration"],
                    "operation_key": key,
                    "operation_version": version,
                },
                "parameters": parameters,
            }
        )

    preparation = PreparationManifest(
        board_id=IDS["board"],
        workspace_id=IDS["workspace"],
        board_version=3,
        draft=BoardDraft(
            name="Shelter surge drill",
            steps=[
                step(
                    "raise",
                    "Raise occupancy",
                    "sql",
                    "capacity.update",
                    "2",
                    {"shelter_id": str(IDS["shelter"]), "occupancy": 108},
                ),
                step(
                    "read_occupancy",
                    "Read occupancy",
                    "sql",
                    "capacity.read",
                    "2",
                    {"shelter_id": str(IDS["shelter"])},
                    ["raise"],
                ),
                step(
                    "open_ticket",
                    "Open resource ticket",
                    "rest",
                    "ticket.create",
                    "1",
                    {"title": "EXERCISE ONLY — cots needed"},
                    ["read_occupancy"],
                ),
                step(
                    "read_ticket",
                    "Read ticket",
                    "rest",
                    "ticket.read",
                    "1",
                    {"record_id": reference("open_ticket", "record_id")},
                    ["open_ticket"],
                ),
            ],
            window=PreparationWindow(
                starts_at=datetime(2026, 9, 23, 12, tzinfo=UTC),
                ends_at=datetime(2026, 9, 23, 14, tzinfo=UTC),
            ),
            recovery="Close exercise tickets and restore seeded occupancy where unchanged.",
        ),
        scenario=ScenarioPin(
            scenario_id=IDS["scenario"],
            revision_version=1,
            content=ScenarioContent(
                title="Shelter surge",
                objectives=[
                    Objective(
                        id=IDS["breach"],
                        title="Breach identified quickly",
                        criterion="Occupancy above 85% is observed within 2 minutes of the change.",
                    ),
                    Objective(
                        id=IDS["acknowledged"],
                        title="Ticket acknowledged",
                        criterion="Someone acknowledges the ticket within 10 minutes of opening.",
                    ),
                ],
            ),
        ),
        assets=[],
        configurations=[sql, rest],
    )
    run_create = {
        "trigger": "manual",
        "observations": [
            {
                "step_id": str(IDS["read_occupancy"]),
                "field": "occupancy_percent",
                "operator": "gt",
                "value": 85,
                "interval_seconds": 10,
                "timeout_seconds": 600,
                "max_samples": 60,
            }
        ],
        "objectives": [
            {
                "objective_id": str(IDS["breach"]),
                "step_id": str(IDS["read_occupancy"]),
                "field": "occupancy_percent",
                "operator": "gt",
                "value": 85,
                "anchor_step_id": str(IDS["raise"]),
                "anchor_field": "committed_at",
                "within_seconds": 120,
            },
            {
                "objective_id": str(IDS["acknowledged"]),
                "step_id": str(IDS["read_ticket"]),
                "field": "acknowledged",
                "operator": "eq",
                "value": True,
                "anchor_step_id": str(IDS["open_ticket"]),
                "anchor_field": "created_at",
                "within_seconds": 600,
                "source_time_field": "acknowledged_at",
            },
        ],
        "recovery": [
            {
                "step_id": str(IDS["open_ticket"]),
                "binding": {
                    "configuration_id": str(IDS["rest_configuration"]),
                    "operation_key": "ticket.close",
                    "operation_version": "1",
                },
                "parameters": {
                    "record_id": reference("open_ticket", "record_id"),
                    "run_id": reference("open_ticket", "run_id"),
                    "expected_version": reference("open_ticket", "record_version"),
                },
                "ownership_parameter": "record_id",
                "version_parameter": "expected_version",
            }
        ],
    }
    return {"preview_manifest": preparation.model_dump(mode="json"), "run_create": run_create}


def serialized(document):
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_golden_run_setup_fixture_is_current():
    expected = serialized(golden_document())
    if os.environ.get("GT_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(expected, encoding="utf-8")
    assert GOLDEN.read_text(encoding="utf-8") == expected


def test_guided_form_bindings_are_executable_without_issues():
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))
    preparation = PreparationManifest.model_validate(document["preview_manifest"])
    bindings = document["run_create"]
    observations = [Observation.model_validate(item) for item in bindings["observations"]]
    objectives = [ObjectiveRule.model_validate(item) for item in bindings["objectives"]]
    recovery = [RecoveryBinding.model_validate(item) for item in bindings["recovery"]]
    assert list(execution_issues(preparation, observations, objectives, recovery)) == []
    manifest = RunManifest(
        run_id=uuid4(),
        preparation=preparation,
        trigger=bindings["trigger"],
        observations=observations,
        objectives=objectives,
        recovery=recovery,
    )
    assert manifest.sql_idempotency == "dispatcher-owned/v1"
