from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from flood_lab.contracts import require_utc
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


def capacity_trigger(occupancy: int, capacity: int) -> bool:
    if capacity <= 0 or not 0 <= occupancy <= capacity:
        raise ValueError("Invalid shelter capacity or occupancy.")
    return occupancy * 100 > capacity * PROFILE["capacity_trigger_percent"]


def timely(start: Evidence | None, observation: Evidence | None, seconds: int) -> Finding:
    if start is None or observation is None:
        return Finding("indeterminate", "Durable start or observation evidence is missing.")
    elapsed = observation.committed_at - start.committed_at
    if elapsed < timedelta(0):
        return Finding("indeterminate", "Evidence precedes its authoritative start.")
    if elapsed <= timedelta(seconds=seconds):
        return Finding("met", "Committed evidence is at or before the inclusive deadline.")
    return Finding("not_met", "Committed evidence is after the inclusive deadline.")


def detection(injected_occupancy: Evidence | None, detected: Evidence | None) -> Finding:
    return timely(injected_occupancy, detected, PROFILE["detect_within_seconds"])


def acknowledgement(created: Evidence | None, acknowledged: Evidence | None) -> Finding:
    return timely(created, acknowledged, PROFILE["acknowledge_within_seconds"])


def allocation(
    created: Evidence | None,
    allocations: list[Evidence] | None,
    requested_quantity: int,
    *,
    evidence_complete: bool,
    observed_through: datetime | None = None,
) -> Finding:
    if requested_quantity <= 0:
        raise ValueError("Requested quantity must be positive.")
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
    deadline = created.committed_at + timedelta(
        seconds=PROFILE["adequate_allocation_within_seconds"]
    )
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
