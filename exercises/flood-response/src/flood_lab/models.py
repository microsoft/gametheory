from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Integer,
    MetaData,
    String,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.mssql import DATETIME2, VARBINARY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    impl = DATETIME2(precision=6)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC timestamp required")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(schema="flood")


class DatabaseIdentity(Base):
    __tablename__ = "database_identity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_version: Mapped[str] = mapped_column(String(32))
    database_name: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(16))
    __table_args__ = (CheckConstraint("id = 1"),)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Unicode(120))
    status: Mapped[str] = mapped_column(String(16), default="active")
    owner_principal: Mapped[str] = mapped_column(Unicode(128))
    owner_principal_sid: Mapped[bytes] = mapped_column(VARBINARY(85))
    owner_operation: Mapped[UUID] = mapped_column(Uuid)
    profile_version: Mapped[str] = mapped_column(String(32))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    seed_manifest: Mapped[str] = mapped_column(UnicodeText)
    record_version: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=text("SYSUTCDATETIME()")
    )
    __table_args__ = (CheckConstraint("status IN ('active','recovered')"),)


class RunGrant(Base):
    __tablename__ = "run_grants"
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("flood.runs.id"), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    object_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    principal_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (
        CheckConstraint("role IN ('participant','api','observer')"),
        CheckConstraint("principal_kind IN ('user','service')"),
        CheckConstraint(
            "(role = 'participant' AND principal_kind = 'user') OR "
            "(role = 'api' AND principal_kind = 'service') OR role = 'observer'"
        ),
    )


class SQLRunGrant(Base):
    __tablename__ = "sql_run_grants"
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("flood.runs.id"), primary_key=True)
    principal_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    principal_sid: Mapped[bytes] = mapped_column(VARBINARY(85))
    capability: Mapped[str] = mapped_column(String(16))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (CheckConstraint("capability IN ('observe','inject')"),)


class Shelter(Base):
    __tablename__ = "shelters"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("flood.runs.id"), index=True)
    name: Mapped[str] = mapped_column(Unicode(120))
    capacity: Mapped[int] = mapped_column(Integer)
    occupancy: Mapped[int] = mapped_column(Integer)
    owner_operation: Mapped[UUID] = mapped_column(Uuid)
    seed_version: Mapped[UUID] = mapped_column(Uuid)
    record_version: Mapped[UUID] = mapped_column(Uuid)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (
        UniqueConstraint("run_id", "id", name="uq_shelter_run_id"),
        CheckConstraint("capacity > 0 AND capacity <= 100000"),
        CheckConstraint("occupancy >= 0 AND occupancy <= capacity"),
    )


class ResourceRequest(Base):
    __tablename__ = "requests"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("flood.runs.id"), index=True)
    shelter_id: Mapped[UUID] = mapped_column(Uuid)
    resource_type: Mapped[str] = mapped_column(String(32))
    quantity_requested: Mapped[int] = mapped_column(Integer)
    quantity_allocated: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="open")
    summary: Mapped[str] = mapped_column(Unicode(240))
    needed_by: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    acknowledged_by: Mapped[str | None] = mapped_column(String(96))
    owner_operation: Mapped[UUID] = mapped_column(Uuid)
    seed_version: Mapped[UUID | None] = mapped_column(Uuid)
    record_version: Mapped[UUID] = mapped_column(Uuid)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (
        UniqueConstraint("run_id", "id", name="uq_request_run_id"),
        ForeignKeyConstraint(
            ["run_id", "shelter_id"],
            ["flood.shelters.run_id", "flood.shelters.id"],
            name="fk_request_shelter_run",
        ),
        CheckConstraint("quantity_requested BETWEEN 1 AND 10000"),
        CheckConstraint("quantity_allocated BETWEEN 0 AND quantity_requested"),
        CheckConstraint("status IN ('open','acknowledged','fulfilled')"),
        CheckConstraint("resource_type IN ('cots','blankets','water_cases','transport_seats')"),
    )


class Allocation(Base):
    __tablename__ = "allocations"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("flood.runs.id"), index=True)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    quantity: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    actor_key: Mapped[str] = mapped_column(String(96))
    owner_operation: Mapped[UUID] = mapped_column(Uuid)
    seed_version: Mapped[UUID | None] = mapped_column(Uuid)
    record_version: Mapped[UUID] = mapped_column(Uuid)
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "request_id"],
            ["flood.requests.run_id", "flood.requests.id"],
            name="fk_allocation_request_run",
        ),
        CheckConstraint("quantity BETWEEN 1 AND 10000"),
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("flood.runs.id"), index=True)
    operation: Mapped[str] = mapped_column(String(64))
    actor_key: Mapped[str] = mapped_column(String(96))
    record_id: Mapped[UUID | None] = mapped_column(Uuid)
    record_version: Mapped[UUID | None] = mapped_column(Uuid)
    outcome: Mapped[str] = mapped_column(String(16))
    committed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    correlation_id: Mapped[UUID] = mapped_column(Uuid)
    data_json: Mapped[str] = mapped_column(UnicodeText)
    __table_args__ = (CheckConstraint("outcome IN ('succeeded','rejected','failed','unknown')"),)


class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    actor_key: Mapped[str] = mapped_column(String(96))
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("flood.runs.id"))
    operation: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128, collation="Latin1_General_100_BIN2"))
    payload_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[str] = mapped_column(UnicodeText)
    http_status: Mapped[int] = mapped_column(Integer)
    event_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("flood.events.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    __table_args__ = (
        UniqueConstraint(
            "actor_key", "run_id", "operation", "idempotency_key", name="uq_receipt_identity"
        ),
    )


class RecoveryEvidence(Base):
    __tablename__ = "recovery_evidence"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("flood.runs.id"), index=True)
    owner_operation: Mapped[UUID] = mapped_column(Uuid)
    operator_principal: Mapped[str] = mapped_column(Unicode(128))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    result_json: Mapped[str] = mapped_column(UnicodeText)
