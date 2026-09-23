"""Real SQL Server milestone evidence. Windows are TEST ONLY; the lab clock is real.

Where a case needs exact deadline equality, a labeled TEST ONLY fixture moves the committed
creation evidence. The production profile keeps its 600/1200 second policy and no shortcuts.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from flood_lab.api import create_app, service
from flood_lab.auth import Actor, current_actor
from flood_lab.config import Settings
from flood_lab.contracts import AcknowledgeRequest, AllocateRequest, OperationError
from flood_lab.database import lock_run, sessions, utc_now, verify_database
from flood_lab.models import Allocation, Event, ResourceRequest

from .test_transactions import create_body

pytestmark = pytest.mark.sql


def created(lab, quantity=24):
    key = "TEST-ONLY-CREATE-" + uuid4().hex
    response = lab.service.create_request(
        lab.api_actor,
        lab.run_id,
        create_body(lab, idempotency_key=key, quantity_requested=quantity),
        uuid4(),
    )
    assert response.status == 201, response.body
    return UUID(response.body["request_id"])


def acknowledge(lab, request_id):
    record = lab.service.request(lab.participant, lab.run_id, request_id)
    response = lab.service.acknowledge(
        lab.participant,
        lab.run_id,
        request_id,
        AcknowledgeRequest(
            idempotency_key="TEST-ONLY-ACK-" + uuid4().hex, expected_version=record.record_version
        ),
        uuid4(),
    )
    assert response.status == 200, response.body
    return response


def allocate(lab, request_id, quantity, available_at=None):
    record = lab.service.request(lab.participant, lab.run_id, request_id)
    body = AllocateRequest(
        idempotency_key="TEST-ONLY-ALLOCATE-" + uuid4().hex,
        expected_version=record.record_version,
        quantity=quantity,
        available_at=available_at or record.created_at,
    )
    response = lab.service.allocate(lab.participant, lab.run_id, request_id, body, uuid4())
    assert response.status == 200, response.body
    return body, response


def milestones(lab, request_id, acknowledge_within=600, allocate_within=1200, actor=None):
    return lab.service.milestones(
        actor or lab.observer, lab.run_id, request_id, acknowledge_within, allocate_within
    )


def after_deadline(lab, request_id, deadline, **windows):
    """TEST ONLY: wait on the real lab clock until as_of is strictly after a short deadline."""
    limit = time.monotonic() + 15
    while time.monotonic() < limit:
        view = milestones(lab, request_id, **windows)
        if view.as_of > getattr(view, deadline):
            return view
        time.sleep(0.1)
    pytest.fail("The lab clock did not pass the TEST ONLY deadline")


def committed(response):
    return datetime.fromisoformat(response.body["committed_at"])


def move_creation(lab, request_id, moment):
    """TEST ONLY clock fixture: place the record and its create event at one exact instant."""
    with sessions(lab.engine).begin() as session:
        record = session.get(ResourceRequest, request_id)
        event = session.scalar(
            select(Event).where(
                Event.run_id == lab.run_id,
                Event.record_id == request_id,
                Event.operation == "request.create",
            )
        )
        record.created_at = event.committed_at = moment


def test_acknowledgement_is_on_time_at_the_inclusive_deadline_and_late_after_it(sql_lab):
    lab = sql_lab
    request_id = created(lab)
    before = milestones(lab, request_id)
    assert before.created_event_id is not None
    assert before.acknowledged is False and before.acknowledged_on_time is None
    response = acknowledge(lab, request_id)
    view = milestones(lab, request_id)
    assert view.acknowledged is True
    assert view.acknowledged_on_time is True
    assert view.acknowledgement_event_id == UUID(response.body["durable_event_id"])
    assert view.acknowledged_at == committed(response)
    assert view.acknowledgement_deadline == view.created_at + timedelta(seconds=600)
    move_creation(lab, request_id, view.acknowledged_at - timedelta(seconds=2))
    assert milestones(lab, request_id, acknowledge_within=2).acknowledged_on_time is True
    late = milestones(lab, request_id, acknowledge_within=1)
    assert late.acknowledged_on_time is False
    assert late.acknowledgement_reason == "Committed evidence is after the inclusive deadline."


def test_acknowledgement_committed_after_a_real_short_window_is_late(sql_lab):
    lab = sql_lab
    request_id = created(lab)
    after_deadline(lab, request_id, "acknowledgement_deadline", acknowledge_within=1)
    acknowledge(lab, request_id)
    assert milestones(lab, request_id, acknowledge_within=1).acknowledged_on_time is False
    assert milestones(lab, request_id, acknowledge_within=3600).acknowledged_on_time is True


def test_absent_acknowledgement_is_null_before_and_after_the_deadline(sql_lab):
    lab = sql_lab
    request_id = created(lab)
    before = milestones(lab, request_id, acknowledge_within=3600, allocate_within=3600)
    assert before.as_of <= before.acknowledgement_deadline
    after = after_deadline(
        lab, request_id, "allocation_deadline", acknowledge_within=1, allocate_within=1
    )
    for view in (before, after):
        assert (view.acknowledged_on_time, view.allocated_on_time) == (None, None)
        assert view.acknowledgement_reason == (
            "No committed acknowledgement; absence alone is not lateness."
        )
        assert view.allocation_reason == "No committed allocation; absence alone is not lateness."
        assert view.acknowledged_at is None and view.acknowledgement_event_id is None


def test_seeded_request_has_no_invented_create_event_or_verdict(sql_lab):
    lab = sql_lab
    acknowledge(lab, lab.request_id)
    allocate(lab, lab.request_id, 40)
    view = milestones(lab, lab.request_id)
    assert view.created_event_id is None
    assert view.acknowledged is True and view.acknowledgement_event_id is not None
    assert view.allocated_total_by_deadline == 40
    assert view.allocation_completed_event_id is not None
    assert (view.acknowledged_on_time, view.allocated_on_time) == (None, None)
    assert view.acknowledgement_reason == view.allocation_reason
    with sessions(lab.engine)() as session:
        operations = set(
            session.scalars(select(Event.operation).where(Event.record_id == lab.request_id))
        )
    assert "request.seed" in operations and "request.create" not in operations


def test_partial_then_complete_allocation_before_the_deadline_is_on_time(sql_lab):
    lab = sql_lab
    request_id = created(lab, quantity=24)
    acknowledge(lab, request_id)
    allocate(lab, request_id, 10)
    partial = milestones(lab, request_id)
    assert partial.allocated_on_time is None
    assert partial.allocated_total_by_deadline == 10
    assert partial.allocation_completed_event_id is None
    _, remainder = allocate(lab, request_id, 14)
    complete = milestones(lab, request_id)
    assert complete.allocated_on_time is True
    assert complete.allocated_total_by_deadline == 24
    assert complete.allocation_event_count == 2
    assert complete.allocation_completed_event_id == UUID(remainder.body["durable_event_id"])
    assert complete.allocation_completed_at == committed(remainder)
    assert complete.status == "fulfilled"


def test_partial_allocation_after_the_deadline_with_complete_history_is_late(sql_lab):
    lab = sql_lab
    request_id = created(lab, quantity=24)
    acknowledge(lab, request_id)
    allocate(lab, request_id, 10)
    before = milestones(lab, request_id, allocate_within=3600)
    assert before.as_of <= before.allocation_deadline
    assert before.allocated_on_time is None
    assert before.allocation_reason == "The observation window has not covered the full deadline."
    after = after_deadline(lab, request_id, "allocation_deadline", allocate_within=3)
    assert after.allocated_on_time is False
    assert after.allocated_total_by_deadline == 10
    assert after.allocation_reason == (
        "Complete evidence does not meet the requested quantity by the deadline."
    )


def test_backdated_availability_cannot_make_a_late_allocation_timely(sql_lab):
    lab = sql_lab
    request_id = created(lab, quantity=5)
    acknowledge(lab, request_id)
    after_deadline(lab, request_id, "allocation_deadline", allocate_within=1)
    record = lab.service.request(lab.participant, lab.run_id, request_id)
    allocate(lab, request_id, 5, available_at=record.created_at)
    late = milestones(lab, request_id, allocate_within=1)
    assert late.allocated_on_time is False
    assert late.allocated_total_by_deadline == 0
    assert late.allocation_completed_at > late.allocation_deadline
    with sessions(lab.engine)() as session:
        stored = session.scalar(select(Allocation).where(Allocation.request_id == request_id))
    assert stored.available_at == record.created_at < late.allocation_completed_at


def test_receipt_replay_and_rejection_are_not_counted_twice(sql_lab):
    lab = sql_lab
    request_id = created(lab, quantity=24)
    acknowledge(lab, request_id)
    body, first = allocate(lab, request_id, 10)
    replay = lab.service.allocate(lab.participant, lab.run_id, request_id, body, uuid4())
    assert replay == first
    current = lab.service.request(lab.participant, lab.run_id, request_id)
    rejected = lab.service.allocate(
        lab.participant,
        lab.run_id,
        request_id,
        AllocateRequest(
            idempotency_key="TEST-ONLY-OVER-ALLOCATION",
            expected_version=current.record_version,
            quantity=100,
            available_at=current.created_at,
        ),
        uuid4(),
    )
    assert rejected.status == 409
    view = milestones(lab, request_id)
    assert view.allocation_event_count == 1
    assert view.allocated_total_by_deadline == view.quantity_allocated == 10
    with sessions(lab.engine)() as session:
        outcomes = list(
            session.scalars(
                select(Event.outcome).where(
                    Event.record_id == request_id, Event.operation == "request.allocate"
                )
            )
        )
        rows = session.scalar(
            select(func.count()).select_from(Allocation).where(Allocation.request_id == request_id)
        )
    assert sorted(outcomes) == ["rejected", "succeeded"]
    assert rows == 1


def test_milestones_use_read_roles_and_do_not_cross_runs(sql_lab):
    lab = sql_lab
    request_id = created(lab)
    for actor in (lab.participant, lab.api_actor, lab.observer):
        assert milestones(lab, request_id, actor=actor).request_id == request_id
    other = lab.operator.seed("test-only-" + uuid4().hex, apply=True)["manifest"]
    other_run, other_request = UUID(other["run_id"]), UUID(other["requests"][0]["id"])
    stranger = Actor(lab.observer.tenant_id, uuid4(), "user")
    for actor, run_id, record_id in (
        (stranger, lab.run_id, request_id),
        (lab.participant, lab.run_id, other_request),
        (lab.participant, other_run, other_request),
        (lab.participant, other_run, request_id),
    ):
        with pytest.raises(OperationError) as error:
            lab.service.milestones(actor, run_id, record_id, 600, 1200)
        assert error.value.status == 404
    lab.operator.grant(
        lab.run_id,
        lab.observer.tenant_id,
        lab.observer.object_id,
        "user",
        "observer",
        lab.expires_at,
        revoke=True,
        apply=True,
    )
    with pytest.raises(OperationError) as error:
        milestones(lab, request_id)
    assert error.value.status == 404


def test_decided_verdicts_do_not_change_as_actions_and_time_advance(sql_lab):
    lab = sql_lab
    request_id = created(lab, quantity=5)
    windows = {"acknowledge_within": 3, "allocate_within": 4}
    history = [milestones(lab, request_id, **windows)]
    acknowledge(lab, request_id)
    history.append(milestones(lab, request_id, **windows))
    allocate(lab, request_id, 2)
    history.append(milestones(lab, request_id, **windows))
    history.append(after_deadline(lab, request_id, "allocation_deadline", **windows))
    allocate(lab, request_id, 3)
    history.append(milestones(lab, request_id, **windows))
    history.append(after_deadline(lab, request_id, "acknowledgement_deadline", **windows))
    for name in ("acknowledged_on_time", "allocated_on_time"):
        decided = [getattr(view, name) for view in history if getattr(view, name) is not None]
        assert decided and len(set(decided)) == 1, (name, decided)
    assert history[-1].acknowledged_on_time is True
    assert history[-1].allocated_on_time is False
    assert history[-1].allocated_total_by_deadline == 2
    assert history[-1].quantity_allocated == 5


def test_milestone_read_waits_for_an_in_flight_writer_clock(sql_lab):
    lab = sql_lab
    request_id = created(lab)
    writer = sessions(lab.engine)()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        verify_database(writer, lab.service.database_name)
        lock_run(writer, lab.run_id)
        writer_clock = utc_now(writer)
        pending = pool.submit(lab.service.milestones, lab.observer, lab.run_id, request_id, 1, 1)
        time.sleep(0.5)
        assert not pending.done()
        # TEST ONLY in-flight writer: it read the clock before the reader and commits later.
        record = writer.get(ResourceRequest, request_id)
        record.status, record.acknowledged_at = "acknowledged", writer_clock
        record.acknowledged_by, record.record_version = lab.participant.key, uuid4()
        event_id = uuid4()
        writer.add(
            Event(
                id=event_id,
                run_id=lab.run_id,
                operation="request.acknowledge",
                actor_key=lab.participant.key,
                record_id=request_id,
                record_version=record.record_version,
                outcome="succeeded",
                committed_at=writer_clock,
                correlation_id=uuid4(),
                data_json="{}",
            )
        )
        writer.commit()
        view = pending.result(timeout=15)
    finally:
        writer.close()
        pool.shutdown(wait=True)
    assert view.as_of >= writer_clock
    assert view.acknowledgement_event_id == event_id
    assert view.acknowledged_at == writer_clock


def test_real_sql_http_milestones_route(sql_lab):
    lab = sql_lab
    request_id = created(lab)
    app = create_app(Settings())
    # TEST ONLY identity override; the data path is the explicitly selected real SQL database.
    app.dependency_overrides[current_actor] = lambda: lab.observer
    app.dependency_overrides[service] = lambda: lab.service
    path = f"/v1/runs/{lab.run_id}/requests/{request_id}/milestones"
    windows = {"acknowledge_within_seconds": 600, "allocate_within_seconds": 1200}
    with TestClient(app) as client:
        response = client.get(path, params=windows)
        assert response.status_code == 200
        body = response.json()
        assert body["contract_version"] == "flood-lab-milestones/v1"
        assert body["request_id"] == str(request_id)
        assert body["created_event_id"] is not None
        assert body["acknowledged_on_time"] is None
        assert body["as_of"].endswith("Z")
        assert client.get(path).status_code == 422
        other = lab.operator.seed("test-only-" + uuid4().hex, apply=True)["manifest"]
        missing = client.get(
            f"/v1/runs/{lab.run_id}/requests/{other['requests'][0]['id']}/milestones",
            params=windows,
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "resource_unavailable"
