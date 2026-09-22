from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, true
from sqlalchemy.orm import Session, sessionmaker

from flood_lab.auth import Actor
from flood_lab.contracts import (
    AcknowledgeRequest,
    AllocateRequest,
    CreateRequest,
    ErrorView,
    EventList,
    EventView,
    MutationView,
    OperationError,
    RequestList,
    RequestView,
    RunList,
    RunView,
    ShelterView,
    parse_version_value,
    version_value,
)
from flood_lab.database import lock_run, locked, utc_now, verify_database
from flood_lab.models import Allocation, Event, Receipt, ResourceRequest, Run, RunGrant, Shelter


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def request_view(record: ResourceRequest) -> RequestView:
    return RequestView(
        request_id=record.id,
        run_id=record.run_id,
        shelter_id=record.shelter_id,
        resource_type=record.resource_type,
        quantity_requested=record.quantity_requested,
        quantity_allocated=record.quantity_allocated,
        status=record.status,
        summary=record.summary,
        needed_by=record.needed_by,
        created_at=record.created_at,
        acknowledged_at=record.acknowledged_at,
        record_version=version_value(record.record_version),
        sequence=record.sequence,
    )


@dataclass(frozen=True)
class OperationResponse:
    status: int
    body: dict[str, Any]

    @property
    def etag(self) -> str | None:
        value = self.body.get("record_version")
        return f'"{value}"' if value is not None else None


Mutation = Callable[
    [Session, datetime, UUID], tuple[ResourceRequest, dict[str, Any], dict[str, Any]]
]


class LabService:
    def __init__(self, factory: sessionmaker[Session], database_name: str):
        self.factory = factory
        self.database_name = database_name

    def _authorize(self, session: Session, actor: Actor, run_id: UUID, action: str) -> RunGrant:
        grant = session.get(RunGrant, (run_id, actor.tenant_id, actor.object_id, actor.kind))
        if grant is None or not grant.active or grant.expires_at <= utc_now(session):
            raise OperationError(
                404, "resource_unavailable", "The run is unavailable to this account."
            )
        allowed = {
            "read": {"participant", "api", "observer"},
            "request.create": {"api"},
            "request.acknowledge": {"participant"},
            "request.allocate": {"participant"},
        }
        if grant.role not in allowed.get(action, set()):
            raise OperationError(
                403, "operation_denied", "This account cannot perform that operation."
            )
        return grant

    def _session(self) -> Session:
        return self.factory()

    def list_runs(self, actor: Actor, limit: int, offset: int) -> RunList:
        with self._session() as session:
            verify_database(session, self.database_name)
            rows = session.execute(
                select(Run, RunGrant)
                .join(RunGrant, RunGrant.run_id == Run.id)
                .where(
                    RunGrant.tenant_id == actor.tenant_id,
                    RunGrant.object_id == actor.object_id,
                    RunGrant.principal_kind == actor.kind,
                    RunGrant.active == true(),
                    RunGrant.expires_at > utc_now(session),
                )
                .order_by(Run.created_at, Run.id)
                .offset(offset)
                .limit(limit + 1)
            ).all()
            return RunList(
                items=[
                    RunView(run_id=run.id, name=run.name, status=run.status, role=grant.role)
                    for run, grant in rows[:limit]
                ],
                next_offset=offset + limit if len(rows) > limit else None,
            )

    def shelters(self, actor: Actor, run_id: UUID) -> list[ShelterView]:
        with self._session() as session:
            verify_database(session, self.database_name)
            self._authorize(session, actor, run_id, "read")
            records = session.scalars(
                select(Shelter)
                .where(Shelter.run_id == run_id, Shelter.retired_at.is_(None))
                .order_by(Shelter.name)
                .limit(100)
            )
            return [
                ShelterView(
                    shelter_id=s.id,
                    run_id=s.run_id,
                    name=s.name,
                    capacity=s.capacity,
                    occupancy=s.occupancy,
                    record_version=version_value(s.record_version),
                )
                for s in records
            ]

    def requests(
        self, actor: Actor, run_id: UUID, limit: int, after: int, status: str | None
    ) -> RequestList:
        with self._session() as session:
            verify_database(session, self.database_name)
            self._authorize(session, actor, run_id, "read")
            query = select(ResourceRequest).where(
                ResourceRequest.run_id == run_id,
                ResourceRequest.retired_at.is_(None),
                ResourceRequest.sequence > after,
            )
            if status is not None:
                query = query.where(ResourceRequest.status == status)
            rows = list(session.scalars(query.order_by(ResourceRequest.sequence).limit(limit + 1)))
            return RequestList(
                items=[request_view(record) for record in rows[:limit]],
                next_after=rows[limit - 1].sequence if len(rows) > limit else None,
            )

    def _request(
        self, session: Session, run_id: UUID, request_id: UUID, *, for_update: bool = False
    ) -> ResourceRequest:
        query = select(ResourceRequest).where(
            ResourceRequest.run_id == run_id,
            ResourceRequest.id == request_id,
            ResourceRequest.retired_at.is_(None),
        )
        record = session.scalar(locked(query) if for_update else query)
        if record is None:
            raise OperationError(
                404, "resource_unavailable", "The request is unavailable in this run."
            )
        return record

    def request(self, actor: Actor, run_id: UUID, request_id: UUID) -> RequestView:
        with self._session() as session:
            verify_database(session, self.database_name)
            self._authorize(session, actor, run_id, "read")
            return request_view(self._request(session, run_id, request_id))

    def events(
        self, actor: Actor, run_id: UUID, limit: int, after: int, record_id: UUID | None
    ) -> EventList:
        with self._session() as session:
            verify_database(session, self.database_name)
            self._authorize(session, actor, run_id, "read")
            query = select(Event).where(Event.run_id == run_id, Event.sequence > after)
            if record_id is not None:
                query = query.where(Event.record_id == record_id)
            records = list(session.scalars(query.order_by(Event.sequence).limit(limit + 1)))
            return EventList(
                items=[
                    EventView(
                        durable_event_id=e.id,
                        sequence=e.sequence,
                        run_id=e.run_id,
                        operation=e.operation,
                        outcome=e.outcome,
                        record_id=e.record_id,
                        record_version=version_value(e.record_version)
                        if e.record_version
                        else None,
                        committed_at=e.committed_at,
                        correlation_id=e.correlation_id,
                        data=json.loads(e.data_json),
                    )
                    for e in records[:limit]
                ],
                next_after=records[limit - 1].sequence if len(records) > limit else None,
            )

    def _mutate(
        self,
        actor: Actor,
        run_id: UUID,
        operation: str,
        key: str,
        payload: dict[str, Any],
        correlation_id: UUID,
        mutation: Mutation,
    ) -> OperationResponse:
        payload_hash = fingerprint(payload)
        with self.factory.begin() as session:
            verify_database(session, self.database_name)
            lock_run(session, run_id)
            self._authorize(session, actor, run_id, operation)
            receipt = session.scalar(
                select(Receipt).where(
                    Receipt.actor_key == actor.key,
                    Receipt.run_id == run_id,
                    Receipt.operation == operation,
                    Receipt.idempotency_key == key,
                )
            )
            # Receipt reconciliation deliberately precedes active-run and current-version checks.
            if receipt is not None:
                if receipt.payload_hash != payload_hash:
                    raise OperationError(
                        409, "idempotency_conflict", "This key was used with different inputs."
                    )
                return OperationResponse(receipt.http_status, json.loads(receipt.response_json))

            now, event_id = utc_now(session), uuid4()
            record_id = UUID(payload["request_id"]) if "request_id" in payload else None
            record_version = None
            try:
                run = session.get(Run, run_id)
                if run is None or run.status != "active":
                    raise OperationError(
                        409, "run_not_active", "This run no longer accepts changes."
                    )
                record, extra, evidence = mutation(session, now, event_id)
                session.flush()
                record_id, record_version = record.id, record.record_version
                body = MutationView(
                    **request_view(record).model_dump(),
                    **extra,
                    durable_event_id=event_id,
                    committed_at=now,
                    correlation_id=correlation_id,
                ).model_dump(mode="json")
                status = 201 if operation == "request.create" else 200
            except OperationError as error:
                body = ErrorView(
                    outcome=error.outcome,
                    code=error.code,
                    message=error.message,
                    correlation_id=correlation_id,
                    durable_event_id=event_id,
                    committed_at=now,
                ).model_dump(mode="json")
                status = error.status
                evidence = {"code": error.code}

            session.add(
                Event(
                    id=event_id,
                    run_id=run_id,
                    operation=operation,
                    actor_key=actor.key,
                    record_id=record_id,
                    record_version=record_version,
                    outcome=body["outcome"],
                    committed_at=now,
                    correlation_id=correlation_id,
                    data_json=canonical(evidence),
                )
            )
            session.flush()
            session.add(
                Receipt(
                    id=uuid4(),
                    actor_key=actor.key,
                    run_id=run_id,
                    operation=operation,
                    idempotency_key=key,
                    payload_hash=payload_hash,
                    response_json=canonical(body),
                    http_status=status,
                    event_id=event_id,
                    created_at=now,
                )
            )
            # A commit error escapes as unknown; no success is returned before this block commits.
        return OperationResponse(status, body)

    def create_request(
        self, actor: Actor, run_id: UUID, body: CreateRequest, correlation_id: UUID
    ) -> OperationResponse:
        def create(session: Session, now: datetime, event_id: UUID):
            shelter = session.scalar(
                select(Shelter)
                .where(
                    Shelter.id == body.shelter_id,
                    Shelter.run_id == run_id,
                    Shelter.retired_at.is_(None),
                )
                .with_hint(Shelter, "WITH (HOLDLOCK)", dialect_name="mssql")
            )
            if shelter is None:
                raise OperationError(
                    404, "resource_unavailable", "The shelter is unavailable in this run."
                )
            if not now <= body.needed_by <= now + timedelta(days=7):
                raise OperationError(
                    422, "invalid_target_time", "Needed-by must be within the next seven UTC days."
                )
            record = ResourceRequest(
                id=uuid4(),
                run_id=run_id,
                shelter_id=shelter.id,
                resource_type=body.resource_type.value,
                quantity_requested=body.quantity_requested,
                quantity_allocated=0,
                status="open",
                summary=body.summary,
                needed_by=body.needed_by,
                created_at=now,
                owner_operation=event_id,
                seed_version=None,
                record_version=uuid4(),
            )
            session.add(record)
            return (
                record,
                {},
                {
                    "resource_type": body.resource_type.value,
                    "quantity_requested": body.quantity_requested,
                    "shelter_id": str(shelter.id),
                },
            )

        return self._mutate(
            actor,
            run_id,
            "request.create",
            body.idempotency_key,
            body.model_dump(mode="json"),
            correlation_id,
            create,
        )

    def acknowledge(
        self,
        actor: Actor,
        run_id: UUID,
        request_id: UUID,
        body: AcknowledgeRequest,
        correlation_id: UUID,
    ) -> OperationResponse:
        def acknowledge(session: Session, now: datetime, event_id: UUID):
            record = self._request(session, run_id, request_id, for_update=True)
            if record.record_version != parse_version_value(body.expected_version):
                raise OperationError(409, "version_conflict", "The request changed. Read it again.")
            if record.status != "open":
                raise OperationError(
                    409, "already_acknowledged", "The request is already acknowledged."
                )
            record.status = "acknowledged"
            record.acknowledged_at, record.acknowledged_by = now, actor.key
            record.record_version = uuid4()
            return record, {}, {"acknowledged_at": now.isoformat()}

        return self._mutate(
            actor,
            run_id,
            "request.acknowledge",
            body.idempotency_key,
            {**body.model_dump(mode="json"), "request_id": str(request_id)},
            correlation_id,
            acknowledge,
        )

    def allocate(
        self,
        actor: Actor,
        run_id: UUID,
        request_id: UUID,
        body: AllocateRequest,
        correlation_id: UUID,
    ) -> OperationResponse:
        def allocate(session: Session, now: datetime, event_id: UUID):
            record = self._request(session, run_id, request_id, for_update=True)
            if record.record_version != parse_version_value(body.expected_version):
                raise OperationError(409, "version_conflict", "The request changed. Read it again.")
            if record.status != "acknowledged":
                raise OperationError(
                    409,
                    "acknowledgement_required",
                    "Acknowledge an open request before allocation.",
                )
            if body.quantity > record.quantity_requested - record.quantity_allocated:
                raise OperationError(
                    409, "quantity_conflict", "Allocation exceeds the remaining requested quantity."
                )
            if not record.created_at <= body.available_at <= now + timedelta(days=7):
                raise OperationError(
                    422, "invalid_target_time", "Availability must be a relevant UTC time."
                )
            allocation_id = uuid4()
            session.add(
                Allocation(
                    id=allocation_id,
                    run_id=run_id,
                    request_id=request_id,
                    quantity=body.quantity,
                    available_at=body.available_at,
                    created_at=now,
                    actor_key=actor.key,
                    owner_operation=event_id,
                    seed_version=None,
                    record_version=uuid4(),
                )
            )
            record.quantity_allocated += body.quantity
            if record.quantity_allocated == record.quantity_requested:
                record.status = "fulfilled"
            record.record_version = uuid4()
            return (
                record,
                {"allocation_id": allocation_id},
                {
                    "allocation_id": str(allocation_id),
                    "quantity": body.quantity,
                    "quantity_allocated": record.quantity_allocated,
                    "available_at": body.available_at.isoformat(),
                },
            )

        return self._mutate(
            actor,
            run_id,
            "request.allocate",
            body.idempotency_key,
            {**body.model_dump(mode="json"), "request_id": str(request_id)},
            correlation_id,
            allocate,
        )
