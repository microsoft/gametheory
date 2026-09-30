from collections.abc import Iterator
from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Unicode,
    UnicodeText,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.dialects.mssql import DATETIME2
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from gametheory.config import get_settings

PREPARATION_DATETIME: DateTime = DATETIME2(precision=6)  # type: ignore[no-untyped-call]


def new_id() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def timestamp(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(Unicode(160))


class Administrator(Base):
    __tablename__ = "administrators"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(Unicode(160))


class Membership(Base):
    __tablename__ = "memberships"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))


class Environment(Base):
    __tablename__ = "environments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(Unicode(160))


class EnvironmentPolicyRecord(Base):
    __tablename__ = "environment_policies"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_environment_policy_version"),
        CheckConstraint(
            "classification IN ('unknown', 'nonproduction', 'production')",
            name="ck_environment_policy_classification",
        ),
        CheckConstraint(
            "classification <> 'production' OR approval_required = 1",
            name="ck_production_requires_approval",
        ),
        CheckConstraint(
            "classification <> 'unknown' OR execution_enabled = 0",
            name="ck_unclassified_execution_disabled",
        ),
    )
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    classification: Mapped[str] = mapped_column(String(16))
    execution_enabled: Mapped[bool] = mapped_column(Boolean)
    approval_required: Mapped[bool] = mapped_column(Boolean)
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class Connection(Base):
    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True)
    environment_id: Mapped[str] = mapped_column(ForeignKey("environments.id"))
    name: Mapped[str] = mapped_column(Unicode(160))
    kind: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Unicode(2000))


class ConnectionGrant(Base):
    __tablename__ = "connection_grants"
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    content: Mapped[str] = mapped_column(UnicodeText)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Revision(Base):
    __tablename__ = "revisions"
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(UnicodeText)
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), index=True)
    base_version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(UnicodeText)
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    name: Mapped[str] = mapped_column(Unicode(160))
    media_type: Mapped[str] = mapped_column(String(100))
    blob_key: Mapped[str] = mapped_column(String(200), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="staged")
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PlanningRequest(Base):
    __tablename__ = "planning_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"), index=True)
    actor: Mapped[str] = mapped_column(String(36))
    base_version: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(UnicodeText)
    context: Mapped[str] = mapped_column(UnicodeText)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    proposal: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    error: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DispatchIntent(Base):
    __tablename__ = "dispatch_intents"
    request_id: Mapped[str] = mapped_column(ForeignKey("planning_requests.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(Unicode(256))
    operation: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(100))
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ConnectionConfigurationRecord(Base):
    __tablename__ = "connection_configurations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "connection_id", "version", name="uq_configuration_version"
        ),
        CheckConstraint("version >= 1", name="ck_configuration_version"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[str] = mapped_column(UnicodeText)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    correlation_id: Mapped[str] = mapped_column(String(36))


class ConfigurationWithdrawal(Base):
    __tablename__ = "configuration_withdrawals"
    configuration_id: Mapped[str] = mapped_column(
        ForeignKey("connection_configurations.id"), primary_key=True
    )
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    correlation_id: Mapped[str] = mapped_column(String(36))


class WorkspaceApproverGrant(Base):
    __tablename__ = "workspace_approver_grants"
    __table_args__ = (Index("ix_approver_workspace_object", "workspace_id", "object_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    object_id: Mapped[str] = mapped_column(String(36))
    granted_by: Mapped[str] = mapped_column(String(36))
    granted_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    correlation_id: Mapped[str] = mapped_column(String(36))


class ApproverGrantRevocation(Base):
    __tablename__ = "approver_grant_revocations"
    grant_id: Mapped[str] = mapped_column(
        ForeignKey("workspace_approver_grants.id"), primary_key=True
    )
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    correlation_id: Mapped[str] = mapped_column(String(36))


class PreparationBoard(Base):
    __tablename__ = "boards"
    __table_args__ = (CheckConstraint("version >= 1", name="ck_board_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(Unicode(160))
    draft: Mapped[str] = mapped_column(UnicodeText)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class BoardOrigin(Base):
    __tablename__ = "board_origins"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scenario_id", "revision_version"], ["revisions.scenario_id", "revisions.version"]
        ),
    )
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"), primary_key=True)
    scenario_id: Mapped[str] = mapped_column(String(36))
    revision_version: Mapped[int] = mapped_column(Integer)
    scenario: Mapped[str] = mapped_column(UnicodeText)
    assets: Mapped[str] = mapped_column(UnicodeText)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class BoardContributor(Base):
    __tablename__ = "board_contributors"
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    first_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    correlation_id: Mapped[str] = mapped_column(String(36))


class PreparationPreview(Base):
    __tablename__ = "board_previews"
    __table_args__ = (UniqueConstraint("board_id", "sequence", name="uq_board_preview_sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"), index=True)
    board_version: Mapped[int] = mapped_column(Integer)
    sequence: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[str] = mapped_column(UnicodeText)
    findings: Mapped[str] = mapped_column(UnicodeText)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    correlation_id: Mapped[str] = mapped_column(String(36))


class PreparationApproval(Base):
    __tablename__ = "board_approvals"
    __table_args__ = (
        UniqueConstraint("board_id", "sequence", name="uq_board_approval_sequence"),
        CheckConstraint("kind = 'preparation'", name="ck_approval_preparation_only"),
        CheckConstraint("execution_authorized = 0", name="ck_approval_execution_disabled"),
        CheckConstraint("acknowledge_unverified = 1", name="ck_approval_acknowledged"),
        CheckConstraint("decision IN ('approved', 'rejected')", name="ck_approval_decision"),
        CheckConstraint("expires_at > created_at", name="ck_approval_expiry"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"), index=True)
    board_version: Mapped[int] = mapped_column(Integer)
    preview_id: Mapped[str] = mapped_column(ForeignKey("board_previews.id"))
    digest: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column(Integer)
    grant_id: Mapped[str] = mapped_column(ForeignKey("workspace_approver_grants.id"))
    reviewer: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(16), default="preparation")
    execution_authorized: Mapped[bool] = mapped_column(Boolean, default=False)
    decision: Mapped[str] = mapped_column(String(16))
    acknowledge_unverified: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME)
    note: Mapped[str] = mapped_column(Unicode(2000))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    correlation_id: Mapped[str] = mapped_column(String(36))


class ApprovalRevocation(Base):
    __tablename__ = "approval_revocations"
    approval_id: Mapped[str] = mapped_column(ForeignKey("board_approvals.id"), primary_key=True)
    reason: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    correlation_id: Mapped[str] = mapped_column(String(36))


class ExecutionGrant(Base):
    __tablename__ = "execution_grants"
    __table_args__ = (
        CheckConstraint("capability IN ('operator', 'reviewer')", name="ck_execution_capability"),
        Index("ix_execution_grant_actor", "workspace_id", "object_id", "capability"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    object_id: Mapped[str] = mapped_column(String(36))
    capability: Mapped[str] = mapped_column(String(16))
    granted_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class ExecutionGrantRevocation(Base):
    __tablename__ = "execution_grant_revocations"
    grant_id: Mapped[str] = mapped_column(ForeignKey("execution_grants.id"), primary_key=True)
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class ExerciseRun(Base):
    __tablename__ = "exercise_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    operator: Mapped[str] = mapped_column(String(36))
    grant_id: Mapped[str] = mapped_column(ForeignKey("execution_grants.id"))
    manifest: Mapped[str] = mapped_column(UnicodeText)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class ExerciseRunState(Base):
    __tablename__ = "exercise_run_states"
    __table_args__ = (
        Index("uq_active_board_run", "board_id", unique=True, mssql_where=text("active = 1")),
        CheckConstraint("version >= 1", name="ck_exercise_run_version"),
        CheckConstraint("phase IN ('exercise', 'recovery')", name="ck_exercise_run_phase"),
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("exercise_runs.id"), primary_key=True)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(32), default="prepared")
    phase: Mapped[str] = mapped_column(String(16), default="exercise")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    context_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class RunAuthorization(Base):
    __tablename__ = "run_authorizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("exercise_runs.id"), index=True)
    phase: Mapped[str] = mapped_column(String(16))
    policy_versions: Mapped[str] = mapped_column(UnicodeText)
    binding_digest: Mapped[str] = mapped_column(String(64))
    target_bindings: Mapped[str] = mapped_column(UnicodeText)
    readiness_ids: Mapped[str] = mapped_column(UnicodeText)
    approval_required: Mapped[bool] = mapped_column(Boolean)
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class RunApproval(Base):
    __tablename__ = "run_approvals"
    __table_args__ = (
        CheckConstraint("decision IN ('approved', 'rejected')", name="ck_run_approval_decision"),
        UniqueConstraint("context_id", "sequence", name="uq_run_approval_sequence"),
        CheckConstraint("sequence >= 1", name="ck_run_approval_sequence"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    context_id: Mapped[str] = mapped_column(ForeignKey("run_authorizations.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(36))
    grant_id: Mapped[str] = mapped_column(ForeignKey("execution_grants.id"))
    decision: Mapped[str] = mapped_column(String(16))
    sequence: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME)
    note: Mapped[str] = mapped_column(Unicode(2000))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class RunApprovalRevocation(Base):
    __tablename__ = "run_approval_revocations"
    approval_id: Mapped[str] = mapped_column(ForeignKey("run_approvals.id"), primary_key=True)
    actor: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class TargetReadiness(Base):
    __tablename__ = "target_readiness"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    configuration_id: Mapped[str] = mapped_column(
        ForeignKey("connection_configurations.id"), index=True
    )
    configuration_digest: Mapped[str] = mapped_column(String(64))
    binding_digest: Mapped[str] = mapped_column(String(64))
    evidence_reference: Mapped[str] = mapped_column(Unicode(512))
    actor: Mapped[str] = mapped_column(Unicode(256))
    checked_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME)
    expires_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME)


class RunStep(Base):
    __tablename__ = "run_steps"
    run_id: Mapped[str] = mapped_column(ForeignKey("exercise_runs.id"), primary_key=True)
    phase: Mapped[str] = mapped_column(String(16), primary_key=True)
    step_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="pending")
    result: Mapped[str] = mapped_column(UnicodeText, default="{}")
    parameters: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    reason: Mapped[str | None] = mapped_column(Unicode(1000), nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime | None] = mapped_column(PREPARATION_DATETIME, nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(PREPARATION_DATETIME, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(PREPARATION_DATETIME, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(PREPARATION_DATETIME, nullable=True)


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("exercise_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48))
    step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    detail: Mapped[str] = mapped_column(UnicodeText)
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)


class RunDispatch(Base):
    __tablename__ = "run_dispatches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("exercise_runs.id"))
    phase: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(PREPARATION_DATETIME, nullable=True)
    control_version: Mapped[int] = mapped_column(Integer, default=0)
    delivered_version: Mapped[int] = mapped_column(Integer, default=0)


class RunSetupRequest(Base):
    """An operator's request for reviewable run-check suggestions; never a run."""

    __tablename__ = "run_setup_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'proposed', 'failed')",
            name="ck_run_setup_request_status",
        ),
        Index("ix_run_setup_requests_board_created", "board_id", "created_at"),
        Index(
            "uq_active_run_setup_request",
            "board_id",
            unique=True,
            mssql_where=text("status IN ('queued', 'running')"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    preview_id: Mapped[str] = mapped_column(ForeignKey("board_previews.id"))
    preview_digest: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(36))
    prompt: Mapped[str] = mapped_column(Unicode(4000))
    context: Mapped[str] = mapped_column(UnicodeText)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    suggestion: Mapped[str | None] = mapped_column(UnicodeText, nullable=True)
    error: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(PREPARATION_DATETIME, nullable=True)


class RunSetupDispatchIntent(Base):
    __tablename__ = "run_setup_dispatch_intents"
    request_id: Mapped[str] = mapped_column(ForeignKey("run_setup_requests.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(PREPARATION_DATETIME, default=now, index=True)
    lease_until: Mapped[datetime | None] = mapped_column(PREPARATION_DATETIME, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Unicode(2000), nullable=True)


@lru_cache
def session_factory() -> sessionmaker[Session]:
    url = get_settings().sql_url
    if not url:
        raise HTTPException(503, "Application SQL database is not configured")
    if not url.startswith("mssql+pyodbc://"):
        raise ValueError("Only the SQL Server pyodbc application provider is supported")
    return sessionmaker(create_engine(url, pool_pre_ping=True, pool_recycle=1800))


def get_db() -> Iterator[Session]:
    with session_factory()() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
