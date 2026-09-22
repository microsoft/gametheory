"""Isolated exercise runs, authorization, evidence, and dispatch state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def ident(name, target=None, primary=False, nullable=False):
    args = [sa.ForeignKey(target)] if target else []
    return sa.Column(name, sa.String(36), *args, primary_key=primary, nullable=nullable)


def clock(name="created_at", nullable=False):
    return sa.Column(name, mssql.DATETIME2(precision=6), nullable=nullable)


def upgrade():
    op.create_table(
        "execution_grants",
        ident("id", primary=True),
        ident("workspace_id", "workspaces.id"),
        ident("object_id"),
        sa.Column("capability", sa.String(16), nullable=False),
        ident("granted_by"),
        clock(),
        sa.CheckConstraint(
            "capability IN ('operator', 'reviewer')", name="ck_execution_capability"
        ),
    )
    op.create_index(
        "ix_execution_grant_actor", "execution_grants", ["workspace_id", "object_id", "capability"]
    )
    op.create_table(
        "execution_grant_revocations",
        ident("grant_id", "execution_grants.id", primary=True),
        ident("actor"),
        clock(),
    )
    op.create_table(
        "exercise_runs",
        ident("id", primary=True),
        ident("board_id", "boards.id"),
        ident("workspace_id", "workspaces.id"),
        ident("operator"),
        ident("grant_id", "execution_grants.id"),
        sa.Column("manifest", sa.UnicodeText(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        clock(),
    )
    op.create_index("ix_exercise_runs_board_id", "exercise_runs", ["board_id"])
    op.create_table(
        "exercise_run_states",
        ident("run_id", "exercise_runs.id", primary=True),
        ident("board_id", "boards.id"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("stop_requested", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Unicode(1000), nullable=True),
        ident("context_id", nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_exercise_run_version"),
        sa.CheckConstraint("phase IN ('exercise', 'recovery')", name="ck_exercise_run_phase"),
    )
    op.create_index(
        "uq_active_board_run",
        "exercise_run_states",
        ["board_id"],
        unique=True,
        mssql_where=sa.text("active = 1"),
    )
    op.create_table(
        "run_authorizations",
        ident("id", primary=True),
        ident("run_id", "exercise_runs.id"),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("policy_versions", sa.UnicodeText(), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("readiness_ids", sa.UnicodeText(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        ident("actor"),
        clock(),
    )
    op.create_index("ix_run_authorizations_run_id", "run_authorizations", ["run_id"])
    op.create_table(
        "run_approvals",
        ident("id", primary=True),
        ident("context_id", "run_authorizations.id"),
        ident("reviewer"),
        ident("grant_id", "execution_grants.id"),
        sa.Column("decision", sa.String(16), nullable=False),
        clock("expires_at"),
        sa.Column("note", sa.Unicode(2000), nullable=False),
        clock(),
        sa.CheckConstraint("decision IN ('approved', 'rejected')", name="ck_run_approval_decision"),
    )
    op.create_index("ix_run_approvals_context_id", "run_approvals", ["context_id"])
    op.create_table(
        "run_approval_revocations",
        ident("approval_id", "run_approvals.id", primary=True),
        ident("actor"),
        clock(),
    )
    op.create_table(
        "target_readiness",
        ident("id", primary=True),
        ident("configuration_id", "connection_configurations.id"),
        sa.Column("configuration_digest", sa.String(64), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("evidence_reference", sa.Unicode(512), nullable=False),
        sa.Column("actor", sa.Unicode(256), nullable=False),
        clock("checked_at"),
        clock("expires_at"),
    )
    op.create_index(
        "ix_target_readiness_configuration_id", "target_readiness", ["configuration_id"]
    )
    op.create_table(
        "run_steps",
        ident("run_id", "exercise_runs.id", primary=True),
        sa.Column("phase", sa.String(16), primary_key=True),
        ident("step_id", primary=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("result", sa.UnicodeText(), nullable=False),
        sa.Column("parameters", sa.UnicodeText(), nullable=True),
        sa.Column("reason", sa.Unicode(1000), nullable=True),
        ident("attempt_id", nullable=True),
        sa.Column("samples", sa.Integer(), nullable=False),
        clock("next_at", True),
        clock("lease_until", True),
        clock("started_at", True),
        clock("finished_at", True),
    )
    op.create_table(
        "run_events",
        ident("id", primary=True),
        ident("run_id", "exercise_runs.id"),
        sa.Column("kind", sa.String(48), nullable=False),
        ident("step_id", nullable=True),
        sa.Column("detail", sa.UnicodeText(), nullable=False),
        clock(),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_table(
        "run_dispatches",
        ident("id", primary=True),
        ident("run_id", "exercise_runs.id"),
        sa.Column("phase", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        clock("next_at"),
        ident("lease_token", nullable=True),
        clock("lease_until", True),
        sa.Column("control_version", sa.Integer(), nullable=False),
        sa.Column("delivered_version", sa.Integer(), nullable=False),
    )


def downgrade():
    for name in (
        "run_dispatches",
        "run_events",
        "run_steps",
        "target_readiness",
        "run_approval_revocations",
        "run_approvals",
        "run_authorizations",
        "exercise_run_states",
        "exercise_runs",
        "execution_grant_revocations",
        "execution_grants",
    ):
        op.drop_table(name)
