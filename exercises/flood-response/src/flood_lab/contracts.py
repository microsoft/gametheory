from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from flood_lab import CONTRACT_VERSION


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Resource(StrEnum):
    cots = "cots"
    blankets = "blankets"
    water_cases = "water_cases"
    transport_seats = "transport_seats"


IdempotencyKey = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
RecordVersion = Annotated[str, Field(pattern=r"^v1:[0-9a-f]{32}$", max_length=35)]
Quantity = Annotated[int, Field(strict=True, ge=1, le=10000)]


def version_tag(version: UUID) -> str:
    return f'"v1:{version.hex}"'


def version_value(version: UUID) -> str:
    return f"v1:{version.hex}"


def parse_version_value(value: str) -> UUID:
    if not re.fullmatch(r"v1:[0-9a-f]{32}", value):
        raise OperationError(
            422, "invalid_version", "An opaque lab record-version value is required."
        )
    return UUID(hex=value[3:])


def parse_version(value: str | None) -> UUID:
    if value is None:
        raise OperationError(428, "precondition_required", "Read the record and send its If-Match.")
    if not re.fullmatch(r'"v1:[0-9a-f]{32}"', value):
        raise OperationError(422, "invalid_precondition", "A single strong lab ETag is required.")
    return UUID(hex=value[4:-1])


def require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("A UTC timestamp with Z or +00:00 is required.")
    return value.astimezone(UTC)


class CreateRequestInput(StrictModel):
    shelter_id: UUID
    resource_type: Resource
    quantity_requested: Quantity
    summary: str = Field(min_length=1, max_length=240)
    needed_by: datetime

    _utc = field_validator("needed_by")(require_utc)


class CreateRequest(CreateRequestInput):
    """Internal command: transport control fields are supplied from validated HTTP headers."""

    idempotency_key: IdempotencyKey


class AcknowledgeRequestInput(StrictModel):
    pass


class AcknowledgeRequest(StrictModel):
    idempotency_key: IdempotencyKey
    expected_version: RecordVersion


class AllocateRequestInput(StrictModel):
    quantity: Quantity
    available_at: datetime

    _utc = field_validator("available_at")(require_utc)


class AllocateRequest(AcknowledgeRequest, AllocateRequestInput):
    pass


class RequestView(StrictModel):
    request_id: UUID
    run_id: UUID
    shelter_id: UUID
    resource_type: Resource
    quantity_requested: int
    quantity_allocated: int
    status: Literal["open", "acknowledged", "fulfilled"]
    summary: str
    needed_by: datetime
    created_at: datetime
    acknowledged_at: datetime | None
    record_version: RecordVersion
    sequence: int


class MutationView(RequestView):
    contract_version: Literal["flood-lab/v1"] = CONTRACT_VERSION
    outcome: Literal["succeeded"] = "succeeded"
    durable_event_id: UUID
    committed_at: datetime
    correlation_id: UUID
    allocation_id: UUID | None = None


class ErrorView(StrictModel):
    contract_version: Literal["flood-lab/v1"] = CONTRACT_VERSION
    outcome: Literal["rejected", "failed", "unknown"]
    code: str
    message: str
    correlation_id: UUID
    durable_event_id: UUID | None = None
    committed_at: datetime | None = None


class RunView(StrictModel):
    run_id: UUID
    name: str
    status: str
    role: Literal["participant", "api", "observer"]


class RunList(StrictModel):
    items: list[RunView]
    next_offset: int | None


class RequestList(StrictModel):
    items: list[RequestView]
    next_after: int | None


class ShelterView(StrictModel):
    shelter_id: UUID
    run_id: UUID
    name: str
    capacity: int
    occupancy: int
    record_version: RecordVersion


class EventView(StrictModel):
    durable_event_id: UUID
    sequence: int
    run_id: UUID
    operation: str
    outcome: Literal["succeeded", "rejected", "failed", "unknown"]
    record_id: UUID | None
    record_version: str | None
    committed_at: datetime
    correlation_id: UUID
    data: dict[str, str | int | bool | None]


class EventList(StrictModel):
    items: list[EventView]
    next_after: int | None


class OperationError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        outcome: Literal["rejected", "failed", "unknown"] = "rejected",
    ):
        self.status = status
        self.code = code
        self.message = message
        self.outcome = outcome
        super().__init__(code)
