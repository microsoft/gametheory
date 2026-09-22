from __future__ import annotations

import re
from uuid import UUID, uuid5

PROFILE_VERSION = "demonstration/v1"
NAMESPACE = UUID("52b1eddb-a00f-5e80-9a3b-d9ed2c4b2f4b")
PROFILE = {
    "schema_version": "flood-lab-profile/v1",
    "profile_version": PROFILE_VERSION,
    "label": "DEMONSTRATION — EXERCISE ONLY — all people and places are fictional",
    "capacity_trigger_percent": 85,
    "capacity_trigger_comparison": "strictly_greater_than",
    "detect_within_seconds": 120,
    "acknowledge_within_seconds": 600,
    "adequate_allocation_within_seconds": 1200,
    "deadline_equality": "timely",
    "missing_evidence": "indeterminate",
    "maximum_initial_notifications": 1,
    "maximum_escalations": 1,
    "maximum_total_notifications": 2,
    "graph_sending_enabled": False,
    "sender": None,
    "recipients": [],
    "trusted_operational_link": None,
    "test_clock_shortcuts": False,
}
SHELTERS = (
    {"key": "aster-reach", "name": "Aster Reach School", "capacity": 120, "occupancy": 84},
    {"key": "reedbank", "name": "Reedbank Community Hall", "capacity": 160, "occupancy": 128},
    {"key": "willow-bend", "name": "Willow Bend Fieldhouse", "capacity": 90, "occupancy": 63},
)
PERSONNEL = (
    {"name": "Aster Vale", "role": "Synthetic shelter coordinator", "shelter_key": "aster-reach"},
    {"name": "Rowan Finch", "role": "Synthetic logistics coordinator", "shelter_key": "reedbank"},
    {"name": "Maren Reed", "role": "Synthetic exercise observer", "shelter_key": "willow-bend"},
)


def seed_manifest(run_key: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", run_key):
        raise ValueError("Run key must be 1–64 lowercase letters, digits or hyphens.")
    run_id = uuid5(NAMESPACE, f"run:{run_key}")
    operation = uuid5(run_id, f"seed:{PROFILE_VERSION}")
    shelters = []
    for item in SHELTERS:
        shelters.append(
            {
                **item,
                "id": str(uuid5(run_id, f"shelter:{item['key']}")),
                "record_version": str(uuid5(operation, f"shelter-version:{item['key']}")),
            }
        )
    return {
        "schema_version": "flood-lab-seed/v1",
        "synthetic": True,
        "profile_version": PROFILE_VERSION,
        "run_key": run_key,
        "run_id": str(run_id),
        "owner_operation": str(operation),
        "name": f"Synthetic Riverwatch — {run_key}",
        "shelters": shelters,
        "requests": [
            {
                "id": str(uuid5(run_id, "request:baseline")),
                "shelter_id": shelters[0]["id"],
                "resource_type": "blankets",
                "quantity_requested": 40,
                "summary": "EXERCISE ONLY — baseline blanket request for synthetic evacuees",
                "needed_by_seconds_after_seed": 1200,
                "record_version": str(uuid5(operation, "request-version:baseline")),
            }
        ],
        "personnel": list(PERSONNEL),
    }
