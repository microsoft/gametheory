from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from flood_lab.contracts import MAX_WINDOW_SECONDS, require_utc
from flood_lab.profile import PROFILE


@dataclass(frozen=True)
class Evidence:
    durable_event_id: UUID
    committed_at: datetime
    quantity: int | None = None

    def __post_init__(self) -> None:
        require_utc(self.committed_at)
        if self.quantity is not None and self.quantity < 0:
            raise ValueError("Evidence quantities cannot be negative.")


def evidence_from_result(result: Mapping[str, object]) -> Evidence | None:
    event_id, timestamp = result.get("durable_event_id"), result.get("committed_at")
    if event_id is None or timestamp is None:
        return None
    try:
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        if not isinstance(timestamp, datetime):
            return None
        return Evidence(UUID(str(event_id)), require_utc(timestamp))
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Finding:
    state: Literal["met", "not_met", "indeterminate"]
    reason: str


def window(seconds: int) -> timedelta:
    if type(seconds) is not int or not 1 <= seconds <= MAX_WINDOW_SECONDS:
        raise ValueError("Assessment windows are whole seconds from 1 to 604800.")
    return timedelta(seconds=seconds)


def capacity_trigger(occupancy: int, capacity: int) -> bool:
    if capacity <= 0 or not 0 <= occupancy <= capacity:
        raise ValueError("Invalid shelter capacity or occupancy.")
    return occupancy * 100 > capacity * PROFILE["capacity_trigger_percent"]


def timely(start: Evidence | None, observation: Evidence | None, seconds: int) -> Finding:
    deadline = window(seconds)
    if start is None or observation is None:
        return Finding("indeterminate", "Durable start or observation evidence is missing.")
    elapsed = observation.committed_at - start.committed_at
    if elapsed < timedelta(0):
        return Finding("indeterminate", "Evidence precedes its authoritative start.")
    if elapsed <= deadline:
        return Finding("met", "Committed evidence is at or before the inclusive deadline.")
    return Finding("not_met", "Committed evidence is after the inclusive deadline.")


def detection(
    injected_occupancy: Evidence | None,
    detected: Evidence | None,
    within_seconds: int = PROFILE["detect_within_seconds"],
) -> Finding:
    return timely(injected_occupancy, detected, within_seconds)


def acknowledgement(
    created: Evidence | None,
    acknowledged: Evidence | None,
    within_seconds: int = PROFILE["acknowledge_within_seconds"],
) -> Finding:
    return timely(created, acknowledged, within_seconds)


def allocation(
    created: Evidence | None,
    allocations: list[Evidence] | None,
    requested_quantity: int,
    *,
    evidence_complete: bool,
    observed_through: datetime | None = None,
    within_seconds: int = PROFILE["adequate_allocation_within_seconds"],
) -> Finding:
    if requested_quantity <= 0:
        raise ValueError("Requested quantity must be positive.")
    limit = window(within_seconds)
    if created is None or allocations is None or not evidence_complete:
        return Finding("indeterminate", "A complete committed allocation history is required.")
    if any(
        item.quantity is None or item.committed_at < created.committed_at for item in allocations
    ):
        return Finding(
            "indeterminate", "Allocation evidence is missing quantities or is inconsistent."
        )
    if len({item.durable_event_id for item in allocations}) != len(allocations):
        return Finding("indeterminate", "Duplicate durable event IDs cannot be counted twice.")
    deadline = created.committed_at + limit
    supplied = sum(item.quantity or 0 for item in allocations if item.committed_at <= deadline)
    if supplied >= requested_quantity:
        return Finding("met", "The requested quantity was allocated by the inclusive deadline.")
    if not allocations:
        return Finding("indeterminate", "No allocation evidence is available.")
    if observed_through is None or require_utc(observed_through) < deadline:
        return Finding("indeterminate", "The observation window has not covered the full deadline.")
    return Finding(
        "not_met", "Complete evidence does not meet the requested quantity by the deadline."
    )


def verdict(finding: Finding) -> bool | None:
    return {"met": True, "not_met": False, "indeterminate": None}[finding.state]


NO_START = "No matching durable request.create event starts the clock."
CLOCK_INCONSISTENT = "Durable evidence is later than the lab clock; timing is inconsistent."
NO_ACKNOWLEDGEMENT = "No committed acknowledgement; absence alone is not lateness."
ACKNOWLEDGEMENT_UNPROVEN = "The acknowledgement record has no matching durable event."
ACKNOWLEDGEMENT_CONFLICT = "The acknowledgement record and durable event disagree."
NO_ALLOCATION = "No committed allocation; absence alone is not lateness."


@dataclass(frozen=True)
class Milestones:
    created: Evidence | None
    acknowledged: Evidence | None
    acknowledgement_deadline: datetime
    allocation_deadline: datetime
    acknowledged_on_time: bool | None
    acknowledgement_reason: str
    allocated_total_by_deadline: int
    allocation_completed: Evidence | None
    allocation_event_count: int
    allocated_on_time: bool | None
    allocation_reason: str


def distinct(items: Iterable[Evidence]) -> tuple[list[Evidence], bool]:
    """Count each durable event ID once; conflicting copies of one ID are inconsistent."""
    seen: dict[UUID, Evidence] = {}
    consistent = True
    for item in items:
        if seen.setdefault(item.durable_event_id, item) != item:
            consistent = False
    return list(seen.values()), consistent


def milestones(
    *,
    created_at: datetime,
    requested_quantity: int,
    allocated_quantity: int,
    record_acknowledged_at: datetime | None,
    creations: Sequence[Evidence],
    acknowledgements: Sequence[Evidence],
    allocation_events: Sequence[Evidence],
    allocation_records: Sequence[Evidence],
    as_of: datetime,
    acknowledge_within_seconds: int = PROFILE["acknowledge_within_seconds"],
    allocate_within_seconds: int = PROFILE["adequate_allocation_within_seconds"],
) -> Milestones:
    """Assess one request from its record, durable events and a consistent lab-clock read.

    Event sequences are in commit order. Allocation records are expressed as evidence keyed
    by their owning durable event ID, so every counted quantity has both a record and event.
    """
    created_at, as_of = require_utc(created_at), require_utc(as_of)
    acknowledgement_deadline = created_at + window(acknowledge_within_seconds)
    allocation_deadline = created_at + window(allocate_within_seconds)
    creation_events, creations_consistent = distinct(creations)
    created = creation_events[0] if creation_events else None
    start = (
        created
        if created is not None
        and creations_consistent
        and len(creation_events) == 1
        and created.committed_at == created_at
        else None
    )
    acknowledgement_events, acknowledgements_consistent = distinct(acknowledgements)
    acknowledged = acknowledgement_events[0] if acknowledgement_events else None
    events, events_consistent = distinct(allocation_events)
    records, records_consistent = distinct(allocation_records)
    owned = {item.durable_event_id: item for item in records}
    complete = (
        events_consistent
        and records_consistent
        and len(records) == len(allocation_records)
        and set(owned) == {item.durable_event_id for item in events}
        and all(item == owned[item.durable_event_id] for item in events)
        and all(item.quantity is not None for item in events)
        and sum(item.quantity or 0 for item in events) == allocated_quantity
    )
    counted = [item for item in events if item.quantity is not None]
    total = sum(item.quantity or 0 for item in counted if item.committed_at <= allocation_deadline)
    completed, running = None, 0
    for item in sorted(counted, key=lambda entry: entry.committed_at):
        running += item.quantity or 0
        if running >= requested_quantity:
            completed = item
            break
    times = [created_at, *(item.committed_at for item in (*creation_events, *events, *records))]
    times += [item.committed_at for item in acknowledgement_events]
    if record_acknowledged_at is not None:
        times.append(require_utc(record_acknowledged_at))
    acknowledgement_verdict: tuple[bool | None, str]
    allocation_verdict: tuple[bool | None, str]
    if max(times) > as_of:
        acknowledgement_verdict = allocation_verdict = (None, CLOCK_INCONSISTENT)
    elif start is None:
        acknowledgement_verdict = allocation_verdict = (None, NO_START)
    else:
        if acknowledged is None:
            acknowledgement_verdict = (
                None,
                NO_ACKNOWLEDGEMENT if record_acknowledged_at is None else ACKNOWLEDGEMENT_UNPROVEN,
            )
        elif not acknowledgements_consistent or record_acknowledged_at != acknowledged.committed_at:
            acknowledgement_verdict = (None, ACKNOWLEDGEMENT_CONFLICT)
        else:
            finding = acknowledgement(start, acknowledged, acknowledge_within_seconds)
            acknowledgement_verdict = (verdict(finding), finding.reason)
        # A writer that starts after this consistent read can still receive the same clock tick
        # as as_of, so coverage is complete only for commits strictly before as_of.
        finding = allocation(
            start,
            events,
            requested_quantity,
            evidence_complete=complete,
            observed_through=as_of - timedelta(microseconds=1),
            within_seconds=allocate_within_seconds,
        )
        absent = complete and not events and finding.state == "indeterminate"
        allocation_verdict = (verdict(finding), NO_ALLOCATION if absent else finding.reason)
    return Milestones(
        created=created,
        acknowledged=acknowledged,
        acknowledgement_deadline=acknowledgement_deadline,
        allocation_deadline=allocation_deadline,
        acknowledged_on_time=acknowledgement_verdict[0],
        acknowledgement_reason=acknowledgement_verdict[1],
        allocated_total_by_deadline=total,
        allocation_completed=completed,
        allocation_event_count=len(events),
        allocated_on_time=allocation_verdict[0],
        allocation_reason=allocation_verdict[1],
    )
