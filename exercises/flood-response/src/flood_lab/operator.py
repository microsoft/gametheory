from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from flood_lab.contracts import OperationError, require_utc, version_tag
from flood_lab.database import (
    database_actor_key,
    lock_run,
    locked,
    operator_identity,
    principal_sid,
    utc_now,
    verify_database,
)
from flood_lab.models import (
    Allocation,
    Event,
    RecoveryEvidence,
    ResourceRequest,
    Run,
    RunGrant,
    Shelter,
    SQLRunGrant,
)
from flood_lab.profile import seed_manifest
from flood_lab.service import canonical, fingerprint

ROOT = Path(__file__).resolve().parents[2]


def migrate(engine, database_name: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_name"] = database_name
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


class Operator:
    def __init__(self, factory: sessionmaker[Session], database_name: str):
        self.factory, self.database_name = factory, database_name

    def _identity(self, session: Session) -> str:
        verify_database(session, self.database_name)
        return operator_identity(session)

    def _owned_run(self, session: Session, run_id: UUID, identity: str) -> Run:
        run = session.scalar(locked(select(Run).where(Run.id == run_id)))
        if (
            run is None
            or run.owner_principal != identity
            or run.owner_principal_sid != principal_sid(session)
        ):
            raise OperationError(
                403, "owner_required", "Only this run's database operator can change it."
            )
        return run

    @staticmethod
    def _event(
        session: Session,
        run_id: UUID,
        identity: str,
        operation: str,
        now: datetime,
        data: dict[str, Any],
        record_id: UUID | None = None,
        record_version: UUID | None = None,
    ) -> UUID:
        event_id = uuid4()
        session.add(
            Event(
                id=event_id,
                run_id=run_id,
                operation=operation,
                actor_key=database_actor_key(session),
                record_id=record_id,
                record_version=record_version,
                outcome="succeeded",
                committed_at=now,
                correlation_id=event_id,
                data_json=canonical(data),
            )
        )
        return event_id

    def seed(self, run_key: str, *, apply: bool = False) -> dict:
        manifest = seed_manifest(run_key)
        digest = fingerprint(manifest)
        run_id, owner = UUID(manifest["run_id"]), UUID(manifest["owner_operation"])
        with self.factory.begin() as session:
            identity = self._identity(session)
            if apply:
                lock_run(session, run_id)
            existing = session.get(Run, run_id)
            if existing is not None:
                if (
                    existing.owner_principal != identity
                    or existing.owner_principal_sid != principal_sid(session)
                    or existing.manifest_hash != digest
                ):
                    raise OperationError(
                        409, "seed_owner_conflict", "Existing run ownership or manifest differs."
                    )
                return {
                    "outcome": "unchanged",
                    "applied": apply,
                    "run_status": existing.status,
                    "manifest_hash": digest,
                    "manifest": manifest,
                    "message": (
                        "Existing records were not overwritten. Use a new run key for a new run."
                    ),
                }
            if not apply:
                return {"outcome": "preview", "manifest_hash": digest, "manifest": manifest}
            now = utc_now(session)
            session.add(
                Run(
                    id=run_id,
                    name=manifest["name"],
                    status="active",
                    owner_principal=identity,
                    owner_principal_sid=principal_sid(session),
                    owner_operation=owner,
                    profile_version=manifest["profile_version"],
                    manifest_hash=digest,
                    seed_manifest=canonical(manifest),
                    record_version=uuid4(),
                    created_at=now,
                )
            )
            session.flush()
            for row in manifest["shelters"]:
                version, record_id = UUID(row["record_version"]), UUID(row["id"])
                session.add(
                    Shelter(
                        id=record_id,
                        run_id=run_id,
                        name=row["name"],
                        capacity=row["capacity"],
                        occupancy=row["occupancy"],
                        owner_operation=owner,
                        seed_version=version,
                        record_version=version,
                    )
                )
                self._event(
                    session,
                    run_id,
                    identity,
                    "shelter.seed",
                    now,
                    {"synthetic": True, "capacity": row["capacity"], "occupancy": row["occupancy"]},
                    record_id,
                    version,
                )
            session.flush()
            for row in manifest["requests"]:
                version, record_id = UUID(row["record_version"]), UUID(row["id"])
                session.add(
                    ResourceRequest(
                        id=record_id,
                        run_id=run_id,
                        shelter_id=UUID(row["shelter_id"]),
                        resource_type=row["resource_type"],
                        quantity_requested=row["quantity_requested"],
                        quantity_allocated=0,
                        status="open",
                        summary=row["summary"],
                        needed_by=now + timedelta(seconds=row["needed_by_seconds_after_seed"]),
                        created_at=now,
                        owner_operation=owner,
                        seed_version=version,
                        record_version=version,
                    )
                )
                self._event(
                    session,
                    run_id,
                    identity,
                    "request.seed",
                    now,
                    {"synthetic": True, "quantity_requested": row["quantity_requested"]},
                    record_id,
                    version,
                )
            event_id = self._event(
                session, run_id, identity, "run.seed", now, {"manifest_hash": digest}
            )
        return {
            "outcome": "succeeded",
            "manifest_hash": digest,
            "durable_event_id": str(event_id),
            "committed_at": now.isoformat(),
            "manifest": manifest,
        }

    def grant(
        self,
        run_id: UUID,
        tenant_id: UUID,
        object_id: UUID,
        principal_kind: str,
        role: str,
        expires_at: datetime,
        *,
        revoke: bool = False,
        apply: bool = False,
    ) -> dict:
        require_utc(expires_at)
        if not (
            (role == "participant" and principal_kind == "user")
            or (role == "api" and principal_kind == "service")
            or (role == "observer" and principal_kind in {"user", "service"})
        ):
            raise ValueError(
                "Participant grants require users; API grants require service principals."
            )
        with self.factory.begin() as session:
            identity = self._identity(session)
            lock_run(session, run_id)
            self._owned_run(session, run_id, identity)
            now = utc_now(session)
            if not revoke and not now < expires_at <= now + timedelta(days=30):
                raise ValueError("Grant expiry must be within the next thirty UTC days.")
            result = {
                "run_id": str(run_id),
                "tenant_id": str(tenant_id),
                "object_id": str(object_id),
                "principal_kind": principal_kind,
                "role": role,
                "active": not revoke,
                "expires_at": expires_at.isoformat(),
                "outcome": "succeeded" if apply else "preview",
            }
            if apply:
                existing = session.get(RunGrant, (run_id, tenant_id, object_id, principal_kind))
                if existing is None:
                    session.add(
                        RunGrant(
                            run_id=run_id,
                            tenant_id=tenant_id,
                            object_id=object_id,
                            principal_kind=principal_kind,
                            role=role,
                            active=not revoke,
                            expires_at=expires_at,
                        )
                    )
                else:
                    existing.role, existing.active = role, not revoke
                    existing.expires_at = expires_at
                result["durable_event_id"] = str(
                    self._event(
                        session,
                        run_id,
                        identity,
                        "grant.revoke" if revoke else "grant.set",
                        now,
                        {
                            "object_id": str(object_id),
                            "role": role,
                            "principal_kind": principal_kind,
                        },
                    )
                )
            return result

    def sql_grant(
        self,
        run_id: UUID,
        principal_name: str,
        capability: str,
        expires_at: datetime,
        *,
        apply: bool = False,
    ) -> dict:
        require_utc(expires_at)
        if capability not in {"observe", "inject"}:
            raise ValueError("SQL capability must be observe or inject.")
        with self.factory.begin() as session:
            identity = self._identity(session)
            lock_run(session, run_id)
            self._owned_run(session, run_id, identity)
            now = utc_now(session)
            if not now < expires_at <= now + timedelta(days=30):
                raise ValueError("SQL grant expiry must be within thirty UTC days.")
            principal_id = session.scalar(text("SELECT USER_ID(:name)"), {"name": principal_name})
            if principal_id is None:
                raise ValueError("Have the database owner provision this principal first.")
            sid = bytes(
                session.scalar(
                    text("SELECT sid FROM sys.database_principals WHERE principal_id=:id"),
                    {"id": principal_id},
                )
            )
            result = {
                "outcome": "succeeded" if apply else "preview",
                "run_id": str(run_id),
                "principal_id": principal_id,
                "principal_name": principal_name,
                "capability": capability,
                "expires_at": expires_at.isoformat(),
            }
            if apply:
                existing = session.get(SQLRunGrant, (run_id, principal_id))
                if existing is None:
                    session.add(
                        SQLRunGrant(
                            run_id=run_id,
                            principal_id=principal_id,
                            principal_sid=sid,
                            capability=capability,
                            expires_at=expires_at,
                        )
                    )
                else:
                    existing.capability, existing.expires_at = capability, expires_at
                    existing.principal_sid = sid
                result["durable_event_id"] = str(
                    self._event(
                        session,
                        run_id,
                        identity,
                        "sql-grant.set",
                        now,
                        {"principal_id": principal_id, "capability": capability},
                    )
                )
            return result

    def recover(self, run_id: UUID, owner_operation: UUID, *, apply: bool = False) -> dict:
        with self.factory.begin() as session:
            identity = self._identity(session)
            if apply:
                lock_run(session, run_id)
            run = self._owned_run(session, run_id, identity)
            if owner_operation != run.owner_operation:
                raise OperationError(
                    409, "recovery_owner_conflict", "Seed ownership does not match."
                )
            now = utc_now(session)
            records: list[dict] = []
            seed = json.loads(run.seed_manifest)
            baseline = {
                Shelter: {UUID(row["id"]): UUID(row["record_version"]) for row in seed["shelters"]},
                ResourceRequest: {
                    UUID(row["id"]): UUID(row["record_version"]) for row in seed["requests"]
                },
                Allocation: {},
            }
            snapshots = {
                Shelter: {UUID(row["id"]): row for row in seed["shelters"]},
                ResourceRequest: {UUID(row["id"]): row for row in seed["requests"]},
                Allocation: {},
            }
            planned_retirements: dict[type, set[UUID]] = {
                Allocation: set(),
                ResourceRequest: set(),
                Shelter: set(),
            }
            for model, kind in (
                (Allocation, "allocation"),
                (ResourceRequest, "request"),
                (Shelter, "shelter"),
            ):
                expected_rows = baseline[model]
                if not expected_rows:
                    continue
                rows = list(
                    session.scalars(
                        locked(
                            select(model).where(model.run_id == run_id, model.id.in_(expected_rows))
                        )
                    )
                )
                missing = set(expected_rows) - {row.id for row in rows}
                for missing_id in sorted(missing, key=str):
                    records.append(
                        {
                            "kind": kind,
                            "record_id": str(missing_id),
                            "state": "conflict",
                            "reason": "owned_record_missing",
                            "expected_version": version_tag(expected_rows[missing_id]),
                            "observed_version": None,
                        }
                    )
                for record in sorted(rows, key=lambda row: str(row.id)):
                    reason = self._recovery_conflict(
                        session,
                        record,
                        planned_retirements,
                        owner_operation,
                        expected_rows[record.id],
                        snapshots[model][record.id],
                        run.created_at,
                    )
                    if record.retired_at is not None and reason is None:
                        state = "already_recovered"
                    elif reason:
                        state = "conflict"
                    else:
                        state = "retired" if apply else "eligible"
                        planned_retirements[model].add(record.id)
                    item = {
                        "kind": kind,
                        "record_id": str(record.id),
                        "state": state,
                        "reason": reason if state == "conflict" else None,
                        "expected_version": version_tag(expected_rows[record.id]),
                        "observed_version": version_tag(record.record_version),
                    }
                    if state == "retired":
                        record.retired_at, record.record_version = now, uuid4()
                    records.append(item)
                session.flush()
            conflicts = sum(row["state"] == "conflict" for row in records)
            result = {
                "outcome": ("partial" if conflicts else "succeeded") if apply else "preview",
                "run_id": str(run_id),
                "owner_operation": str(owner_operation),
                "conflicts": conflicts,
                "records": records,
                "retained_evidence": True,
                "email_rollback": False,
                "policy": "Retire unchanged seed-owned data only; retain all other operation data.",
            }
            if apply:
                active_rows = sum(
                    session.scalar(
                        select(func.count())
                        .select_from(model)
                        .where(model.run_id == run_id, model.retired_at.is_(None))
                    )
                    or 0
                    for model in (Shelter, ResourceRequest, Allocation)
                )
                if not active_rows:
                    run.status, run.record_version = "recovered", uuid4()
                evidence_id = uuid4()
                result["recovery_evidence_id"] = str(evidence_id)
                result["committed_at"] = now.isoformat()
                result["durable_event_id"] = str(
                    self._event(
                        session,
                        run_id,
                        identity,
                        "run.recover",
                        now,
                        {"conflicts": conflicts, "recovery_evidence_id": str(evidence_id)},
                    )
                )
                session.add(
                    RecoveryEvidence(
                        id=evidence_id,
                        run_id=run_id,
                        owner_operation=owner_operation,
                        operator_principal=identity,
                        created_at=now,
                        result_json=canonical(result),
                    )
                )
        return result

    @staticmethod
    def _recovery_conflict(
        session: Session,
        record: Allocation | ResourceRequest | Shelter,
        planned: dict[type, set[UUID]],
        owner_operation: UUID,
        expected_version: UUID,
        snapshot: dict,
        seed_created_at: datetime,
    ) -> str | None:
        if record.owner_operation != owner_operation:
            return "ownership_changed"
        if record.retired_at is not None:
            return None
        if record.record_version != expected_version or record.seed_version != expected_version:
            return "changed_since_seed"
        if isinstance(record, Shelter) and any(
            getattr(record, name) != snapshot[name] for name in ("name", "capacity", "occupancy")
        ):
            return "seed_snapshot_changed"
        if isinstance(record, ResourceRequest):
            if (
                any(
                    getattr(record, name) != snapshot[name]
                    for name in ("resource_type", "quantity_requested", "summary")
                )
                or record.shelter_id != UUID(snapshot["shelter_id"])
                or record.quantity_allocated != 0
                or record.status != "open"
                or record.acknowledged_at is not None
                or record.acknowledged_by is not None
                or record.created_at != seed_created_at
                or record.needed_by
                != seed_created_at + timedelta(seconds=snapshot["needed_by_seconds_after_seed"])
            ):
                return "seed_snapshot_changed"
        if isinstance(record, ResourceRequest):
            dependencies = session.scalars(
                locked(
                    select(Allocation.id).where(
                        Allocation.run_id == record.run_id,
                        Allocation.request_id == record.id,
                        Allocation.retired_at.is_(None),
                    )
                )
            )
            if any(item not in planned[Allocation] for item in dependencies):
                return "allocation_reference_retained"
        if isinstance(record, Shelter):
            dependencies = session.scalars(
                locked(
                    select(ResourceRequest.id).where(
                        ResourceRequest.run_id == record.run_id,
                        ResourceRequest.shelter_id == record.id,
                        ResourceRequest.retired_at.is_(None),
                    )
                )
            )
            if any(item not in planned[ResourceRequest] for item in dependencies):
                return "request_reference_retained"
        return None
