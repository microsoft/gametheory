from typing import Literal, Self
from uuid import UUID

from fastapi import HTTPException
from pydantic import StrictBool, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from gametheory.auth import Principal, require_admin
from gametheory.domain import Contract, Name
from gametheory.persistence import (
    Environment,
    EnvironmentPolicyRecord,
    ExerciseRun,
    RunDispatch,
    Workspace,
    timestamp,
)
from gametheory.service import audit


class EnvironmentPolicyInput(Contract):
    classification: Literal["unknown", "nonproduction", "production"]
    execution_enabled: StrictBool = False
    approval_required: StrictBool = False

    @model_validator(mode="after")
    def approval_boundary(self) -> Self:
        if self.classification == "production" and not self.approval_required:
            raise ValueError("Production always requires execution approval")
        if self.classification == "unknown" and self.execution_enabled:
            raise ValueError("Classify this environment before allowing execution")
        return self


class EnvironmentPolicyView(EnvironmentPolicyInput):
    environment_id: UUID
    name: Name
    version: int
    updated_by: UUID
    updated_at: str


def latest_policy(db: Session, environment_id: str) -> EnvironmentPolicyRecord:
    record = db.scalar(
        select(EnvironmentPolicyRecord)
        .where(EnvironmentPolicyRecord.environment_id == environment_id)
        .order_by(EnvironmentPolicyRecord.version.desc())
        .limit(1)
        .with_hint(EnvironmentPolicyRecord, "WITH (HOLDLOCK)", dialect_name="mssql")
        .execution_options(populate_existing=True)
    )
    if record is None:
        raise HTTPException(409, "Environment policy is missing; apply the policy migration")
    return record


def policy_view(environment: Environment, record: EnvironmentPolicyRecord) -> EnvironmentPolicyView:
    return EnvironmentPolicyView.model_validate(
        {
            "environment_id": environment.id,
            "name": environment.name,
            "version": record.version,
            "classification": record.classification,
            "execution_enabled": record.execution_enabled,
            "approval_required": record.approval_required,
            "updated_by": record.actor,
            "updated_at": timestamp(record.created_at),
        }
    )


def policy_environment(
    db: Session, actor: Principal, eid: str, *, mutation: bool = False
) -> Environment:
    require_admin(db, actor, fence=True)
    query = select(Environment).where(
        Environment.id == eid, Environment.organization_id == actor.tenant
    )
    environment = db.scalar(
        query.with_hint(
            Environment,
            "WITH (UPDLOCK, HOLDLOCK)" if mutation else "WITH (HOLDLOCK)",
            dialect_name="mssql",
        ).execution_options(populate_existing=True)
    )
    if environment is None:
        raise HTTPException(404, "Environment not found")
    return environment


def save_policy(
    db: Session,
    actor: Principal,
    eid: str,
    body: EnvironmentPolicyInput,
    version: int,
    correlation: str,
) -> EnvironmentPolicyView:
    environment = policy_environment(db, actor, eid, mutation=True)
    previous = latest_policy(db, eid)
    if version != previous.version:
        raise HTTPException(409, "Environment policy changed. Reload before saving.")
    if previous.classification == "production" and body.classification != "production":
        raise HTTPException(422, "A production environment cannot be downgraded to bypass approval")
    record = EnvironmentPolicyRecord(
        environment_id=eid,
        version=previous.version + 1,
        **body.model_dump(),
        actor=actor.object_id,
    )
    db.add(record)
    db.flush()
    db.execute(
        update(RunDispatch)
        .where(
            RunDispatch.run_id.in_(
                select(ExerciseRun.id)
                .join(Workspace, Workspace.id == ExerciseRun.workspace_id)
                .where(Workspace.organization_id == actor.tenant)
            )
        )
        .values(control_version=RunDispatch.control_version + 1)
    )
    audit(
        db,
        actor,
        "environment.policy_changed",
        eid,
        version=record.version,
        correlation=correlation,
    )
    return policy_view(environment, record)
