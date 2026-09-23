"""Pure milestone rules. TEST ONLY clocks: explicit UTC instants, never the live profile."""

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from flood_lab.assessment import (
    ACKNOWLEDGEMENT_CONFLICT,
    ACKNOWLEDGEMENT_UNPROVEN,
    CLOCK_INCONSISTENT,
    NO_ACKNOWLEDGEMENT,
    NO_ALLOCATION,
    NO_START,
    Evidence,
    acknowledgement,
    allocation,
    milestones,
    window,
)
from flood_lab.profile import PROFILE

CREATED = datetime(2030, 1, 1, 10, tzinfo=UTC)


def at(seconds):
    return CREATED + timedelta(seconds=seconds)


def event(seconds, quantity=None):
    return Evidence(uuid4(), at(seconds), quantity)


def assess(
    *,
    as_of=0,
    created=True,
    acknowledged=None,
    allocations=(),
    records=None,
    requested=24,
    allocated=None,
    record_acknowledged_at="event",
    ack=10,
    alloc=20,
):
    """TEST ONLY: 10 s acknowledgement and 20 s allocation windows unless a case overrides."""
    allocations = list(allocations)
    if records is None:
        records = allocations
    if allocated is None:
        allocated = sum(
            item.quantity or 0
            for item in {item.durable_event_id: item for item in records}.values()
        )
    if record_acknowledged_at == "event":
        record_acknowledged_at = acknowledged.committed_at if acknowledged else None
    return milestones(
        created_at=CREATED,
        requested_quantity=requested,
        allocated_quantity=allocated,
        record_acknowledged_at=record_acknowledged_at,
        creations=[Evidence(uuid4(), CREATED)] if created else [],
        acknowledgements=[acknowledged] if acknowledged else [],
        allocation_events=allocations,
        allocation_records=records,
        as_of=at(as_of),
        acknowledge_within_seconds=ack,
        allocate_within_seconds=alloc,
    )


def test_profile_keeps_documented_defaults_while_windows_are_arguments():
    assert PROFILE["acknowledge_within_seconds"] == 600
    assert PROFILE["adequate_allocation_within_seconds"] == 1200
    assert PROFILE["test_clock_shortcuts"] is False
    start = event(0)
    assert acknowledgement(start, event(600)).state == "met"
    assert acknowledgement(start, event(11), 10).state == "not_met"
    assert allocation(
        start, [event(21, 24)], 24, evidence_complete=True, within_seconds=20
    ).state == ("indeterminate")
    for invalid in (0, 604801, True, 1.5):
        with pytest.raises(ValueError):
            window(invalid)


def test_acknowledgement_on_time_at_deadline_and_late():
    for offset, expected in ((3, True), (10, True), (10.000001, False)):
        result = assess(as_of=30, acknowledged=event(offset))
        assert result.acknowledged_on_time is expected
        assert result.acknowledged.committed_at == at(offset)
    assert result.acknowledgement_deadline == at(10)
    assert result.acknowledgement_reason == "Committed evidence is after the inclusive deadline."


@pytest.mark.parametrize("as_of", [5, 10, 11, 3600])
def test_absent_acknowledgement_is_never_lateness(as_of):
    result = assess(as_of=as_of)
    assert result.acknowledged_on_time is None
    assert result.acknowledgement_reason == NO_ACKNOWLEDGEMENT
    assert result.allocated_on_time is None
    assert result.allocation_reason == NO_ALLOCATION


def test_request_without_create_event_has_no_verdicts_or_invented_start():
    result = assess(
        as_of=3600, created=False, acknowledged=event(1), allocations=[event(2, 24)], requested=24
    )
    assert result.created is None
    assert result.acknowledged is not None
    assert (result.acknowledged_on_time, result.allocated_on_time) == (None, None)
    assert result.acknowledgement_reason == result.allocation_reason == NO_START
    assert result.allocated_total_by_deadline == 24
    mismatched = milestones(
        created_at=CREATED,
        requested_quantity=1,
        allocated_quantity=0,
        record_acknowledged_at=None,
        creations=[Evidence(uuid4(), at(1))],
        acknowledgements=[],
        allocation_events=[],
        allocation_records=[],
        as_of=at(3600),
    )
    assert mismatched.created is not None
    assert mismatched.acknowledgement_reason == NO_START


def test_partial_then_complete_allocation_before_deadline_is_met():
    partial = [event(4, 10)]
    before = assess(as_of=5, acknowledged=event(1), allocations=partial)
    assert before.allocated_on_time is None
    assert before.allocated_total_by_deadline == 10
    assert before.allocation_completed is None
    complete = [*partial, event(20, 14)]
    after = assess(as_of=40, acknowledged=event(1), allocations=complete)
    assert after.allocated_on_time is True
    assert after.allocated_total_by_deadline == 24
    assert after.allocation_completed == complete[1]
    assert after.allocation_event_count == 2
    assert after.allocation_deadline == at(20)


def test_partial_allocation_after_covered_deadline_is_committed_lateness():
    partial = [event(4, 10)]
    at_deadline = assess(as_of=20, allocations=partial)
    assert at_deadline.allocated_on_time is None
    covered = assess(as_of=20.000001, allocations=partial)
    assert covered.allocated_on_time is False
    assert covered.allocation_reason == (
        "Complete evidence does not meet the requested quantity by the deadline."
    )
    incomplete = assess(as_of=60, allocations=partial, allocated=11)
    assert incomplete.allocated_on_time is None


def test_only_commit_time_counts_so_backdated_availability_cannot_prove_timeliness():
    # The target availability time is not evidence; this committed event is after the deadline.
    late = [event(21, 24)]
    result = assess(as_of=21, allocations=late)
    assert result.allocated_on_time is False
    assert result.allocated_total_by_deadline == 0
    assert result.allocation_completed == late[0]


def test_replayed_or_duplicate_durable_event_is_counted_once():
    shared, second = event(3, 10), event(5, 14)
    result = assess(as_of=40, allocations=[shared, shared, second], records=[shared, second])
    assert result.allocation_event_count == 2
    assert result.allocated_total_by_deadline == 24
    assert result.allocated_on_time is True
    conflicting = Evidence(shared.durable_event_id, shared.committed_at, 11)
    result = assess(as_of=40, allocations=[shared, conflicting, second], records=[shared, second])
    assert result.allocated_on_time is None
    assert result.allocated_total_by_deadline == 24


def test_allocation_history_must_pair_every_record_with_its_durable_event():
    first, second = event(3, 10), event(5, 14)
    cases = [
        {"records": [first]},
        {"records": [first, second, event(6, 1)]},
        {"records": [first, Evidence(second.durable_event_id, at(7), 14)]},
        {"records": [first, first, second]},
        {"allocated": 23},
    ]
    for case in cases:
        result = assess(as_of=40, allocations=[first, second], **case)
        assert result.allocated_on_time is None, case
    unknown_quantity = Evidence(uuid4(), at(2))
    result = assess(as_of=40, allocations=[unknown_quantity], allocated=0)
    assert result.allocated_on_time is None


def test_acknowledgement_record_must_match_its_durable_event():
    acknowledged = event(3)
    assert assess(
        as_of=40, acknowledged=acknowledged, record_acknowledged_at=at(4)
    ).acknowledgement_reason == (ACKNOWLEDGEMENT_CONFLICT)
    assert (
        assess(
            as_of=40, acknowledged=acknowledged, record_acknowledged_at=None
        ).acknowledged_on_time
        is None
    )
    unproven = assess(as_of=40, record_acknowledged_at=at(3))
    assert unproven.acknowledged_on_time is None
    assert unproven.acknowledgement_reason == ACKNOWLEDGEMENT_UNPROVEN


def test_evidence_later_than_the_lab_clock_is_inconsistent_not_decided():
    result = assess(as_of=2, acknowledged=event(3), allocations=[event(3, 24)])
    assert (result.acknowledged_on_time, result.allocated_on_time) == (None, None)
    assert result.acknowledgement_reason == result.allocation_reason == CLOCK_INCONSISTENT


def test_decided_verdicts_never_change_as_time_and_commits_advance():
    rng = random.Random(20300101)
    for _ in range(200):
        requested = rng.randint(1, 6)
        actions = sorted(rng.uniform(0, 40) for _ in range(rng.randint(0, 5)))
        acknowledged = event(actions[0]) if actions and rng.random() < 0.8 else None
        allocations, remaining = [], requested
        for moment in actions[1:] if acknowledged else []:
            if remaining:
                quantity = rng.randint(1, remaining)
                allocations.append(event(moment, quantity))
                remaining -= quantity
        decided = {}
        for step in range(0, 460):
            now = step / 10
            visible_ack = (
                acknowledged if acknowledged and acknowledged.committed_at <= at(now) else None
            )
            visible = [item for item in allocations if item.committed_at <= at(now)]
            result = assess(
                as_of=now, acknowledged=visible_ack, allocations=visible, requested=requested
            )
            for name in ("acknowledged_on_time", "allocated_on_time"):
                value = getattr(result, name)
                if name in decided:
                    assert value == decided[name], (name, now, requested, acknowledged, allocations)
                elif value is not None:
                    decided[name] = value
