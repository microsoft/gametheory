"""Add preparation-only configuration, boards, grants and immutable review history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "connection_configurations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("connection_id", sa.String(36), sa.ForeignKey("connections.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.UnicodeText(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.UniqueConstraint(
            "workspace_id", "connection_id", "version", name="uq_configuration_version"
        ),
        sa.CheckConstraint("version >= 1", name="ck_configuration_version"),
    )
    op.create_index(
        "ix_connection_configurations_workspace_id", "connection_configurations", ["workspace_id"]
    )
    op.create_table(
        "configuration_withdrawals",
        sa.Column(
            "configuration_id",
            sa.String(36),
            sa.ForeignKey("connection_configurations.id"),
            primary_key=True,
        ),
        sa.Column("actor", sa.String(36), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
    )
    op.create_table(
        "workspace_approver_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("object_id", sa.String(36), nullable=False),
        sa.Column("granted_by", sa.String(36), nullable=False),
        sa.Column("granted_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
    )
    op.create_index(
        "ix_approver_workspace_object", "workspace_approver_grants", ["workspace_id", "object_id"]
    )
    op.create_table(
        "approver_grant_revocations",
        sa.Column(
            "grant_id",
            sa.String(36),
            sa.ForeignKey("workspace_approver_grants.id"),
            primary_key=True,
        ),
        sa.Column("actor", sa.String(36), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
    )
    op.create_table(
        "boards",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workspace_id", sa.String(36), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("name", sa.Unicode(160), nullable=False),
        sa.Column("draft", sa.UnicodeText(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_board_version"),
    )
    op.create_index("ix_boards_workspace_id", "boards", ["workspace_id"])
    op.create_table(
        "board_origins",
        sa.Column("board_id", sa.String(36), sa.ForeignKey("boards.id"), primary_key=True),
        sa.Column("scenario_id", sa.String(36), nullable=False),
        sa.Column("revision_version", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.UnicodeText(), nullable=False),
        sa.Column("assets", sa.UnicodeText(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id", "revision_version"], ["revisions.scenario_id", "revisions.version"]
        ),
    )
    op.create_table(
        "board_contributors",
        sa.Column("board_id", sa.String(36), sa.ForeignKey("boards.id"), primary_key=True),
        sa.Column("object_id", sa.String(36), primary_key=True),
        sa.Column("first_version", sa.Integer(), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
    )
    op.create_table(
        "board_previews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("board_id", sa.String(36), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column("board_version", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("manifest", sa.UnicodeText(), nullable=False),
        sa.Column("findings", sa.UnicodeText(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("board_id", "sequence", name="uq_board_preview_sequence"),
    )
    op.create_index("ix_board_previews_board_id", "board_previews", ["board_id"])
    op.create_table(
        "board_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("board_id", sa.String(36), sa.ForeignKey("boards.id"), nullable=False),
        sa.Column("board_version", sa.Integer(), nullable=False),
        sa.Column("preview_id", sa.String(36), sa.ForeignKey("board_previews.id"), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "grant_id", sa.String(36), sa.ForeignKey("workspace_approver_grants.id"), nullable=False
        ),
        sa.Column("reviewer", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("execution_authorized", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("acknowledge_unverified", sa.Boolean(), nullable=False),
        sa.Column("expires_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.Column("note", sa.Unicode(2000), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("board_id", "sequence", name="uq_board_approval_sequence"),
        sa.CheckConstraint("kind = 'preparation'", name="ck_approval_preparation_only"),
        sa.CheckConstraint("execution_authorized = 0", name="ck_approval_execution_disabled"),
        sa.CheckConstraint("acknowledge_unverified = 1", name="ck_approval_acknowledged"),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_approval_decision"),
        sa.CheckConstraint("expires_at > created_at", name="ck_approval_expiry"),
    )
    op.create_index("ix_board_approvals_board_id", "board_approvals", ["board_id"])
    op.create_table(
        "approval_revocations",
        sa.Column(
            "approval_id", sa.String(36), sa.ForeignKey("board_approvals.id"), primary_key=True
        ),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(36), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
    )


def downgrade():
    for table in (
        "approval_revocations",
        "board_approvals",
        "board_previews",
        "board_contributors",
        "board_origins",
        "boards",
        "approver_grant_revocations",
        "workspace_approver_grants",
        "configuration_withdrawals",
        "connection_configurations",
    ):
        op.drop_table(table)
