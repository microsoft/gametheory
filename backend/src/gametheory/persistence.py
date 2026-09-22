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
