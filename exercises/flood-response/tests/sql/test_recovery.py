from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from flood_lab.contracts import AcknowledgeRequest
from flood_lab.database import sessions
from flood_lab.models import Event, ResourceRequest, Run, Shelter

from .test_transactions import create_body

pytestmark = pytest.mark.sql


def owner(lab):
    return UUID(lab.manifest["owner_operation"])


def test_seed_preview_replay_and_recovery_are_repeatable(sql_lab):
    lab = sql_lab
    assert lab.operator.seed(lab.run_key)["outcome"] == "unchanged"
    assert lab.operator.seed(lab.run_key, apply=True)["outcome"] == "unchanged"
    preview = lab.operator.recover(lab.run_id, owner(lab))
    assert preview["outcome"] == "preview"
    assert all(row["state"] == "eligible" for row in preview["records"])
    assert [row["kind"] for row in preview["records"]] == [
        "request",
        "shelter",
        "shelter",
        "shelter",
    ]
    with sessions(lab.engine)() as session:
        before_events = session.scalar(
            select(func.count()).select_from(Event).where(Event.run_id == lab.run_id)
        )
    result = lab.operator.recover(lab.run_id, owner(lab), apply=True)
    assert result["outcome"] == "succeeded"
    assert all(row["state"] == "retired" for row in result["records"])
    again = lab.operator.recover(lab.run_id, owner(lab), apply=True)
    assert all(row["state"] == "already_recovered" for row in again["records"])
    with sessions(lab.engine)() as session:
        assert session.get(Run, lab.run_id).status == "recovered"
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.run_id == lab.run_id)
            )
            == before_events + 2
        )
    assert lab.operator.seed(lab.run_key, apply=True)["run_status"] == "recovered"


def test_human_change_after_preview_is_preserved_and_reported(sql_lab):
    lab = sql_lab
    lab.operator.recover(lab.run_id, owner(lab))
    record = lab.service.request(lab.participant, lab.run_id, lab.request_id)
    lab.service.acknowledge(
        lab.participant,
        lab.run_id,
        lab.request_id,
        AcknowledgeRequest(
            idempotency_key="TEST-ONLY-HUMAN", expected_version=record.record_version
        ),
        uuid4(),
    )
    result = lab.operator.recover(lab.run_id, owner(lab), apply=True)
    assert result["outcome"] == "partial"
    reasons = {row["reason"] for row in result["records"]}
    assert "changed_since_seed" in reasons
    assert "request_reference_retained" in reasons
    assert lab.service.request(lab.participant, lab.run_id, lab.request_id).status == "acknowledged"
    with sessions(lab.engine)() as session:
        assert session.get(Shelter, lab.shelter_id).retired_at is None


def test_other_operation_reference_blocks_parent_recovery(sql_lab):
    lab = sql_lab
    second_shelter = UUID(lab.manifest["shelters"][1]["id"])
    response = lab.service.create_request(
        lab.api_actor, lab.run_id, create_body(lab, shelter_id=second_shelter), uuid4()
    )
    assert response.status == 201
    result = lab.operator.recover(lab.run_id, owner(lab), apply=True)
    retained = next(row for row in result["records"] if row["record_id"] == str(second_shelter))
    assert retained["reason"] == "request_reference_retained"
    with sessions(lab.engine)() as session:
        assert session.get(ResourceRequest, UUID(response.body["request_id"])).retired_at is None


def test_changed_ownership_is_a_per_record_conflict(sql_lab):
    lab = sql_lab
    with sessions(lab.engine).begin() as session:
        session.get(Shelter, lab.shelter_id).owner_operation = uuid4()
    result = lab.operator.recover(lab.run_id, owner(lab), apply=True)
    entry = next(row for row in result["records"] if row["record_id"] == str(lab.shelter_id))
    assert entry["state"] == "conflict"
    assert entry["reason"] == "ownership_changed"


def test_out_of_band_human_change_without_version_bump_is_preserved(sql_lab):
    lab = sql_lab
    with sessions(lab.engine).begin() as session:
        session.get(Shelter, lab.shelter_id).occupancy = 90
    result = lab.operator.recover(lab.run_id, owner(lab), apply=True)
    entry = next(row for row in result["records"] if row["record_id"] == str(lab.shelter_id))
    assert entry["state"] == "conflict"
    assert entry["reason"] == "seed_snapshot_changed"
