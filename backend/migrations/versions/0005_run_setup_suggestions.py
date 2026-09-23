"""Reviewed run-check suggestions and their planning-worker dispatch intents."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def ident(name, target=None, primary=False, nullable=False):
    args = [sa.ForeignKey(target)] if target else []
    return sa.Column(name, sa.String(36), *args, primary_key=primary, nullable=nullable)


def clock(name="created_at", nullable=False):
    return sa.Column(name, mssql.DATETIME2(precision=6), nullable=nullable)


def upgrade():
    op.create_table(
        "run_setup_requests",
        ident("id", primary=True),
        ident("board_id", "boards.id"),
        ident("workspace_id", "workspaces.id"),
        ident("preview_id", "board_previews.id"),
        sa.Column("preview_digest", sa.String(64), nullable=False),
        ident("actor"),
        sa.Column("prompt", sa.Unicode(4000), nullable=False),
        sa.Column("context", sa.UnicodeText(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("suggestion", sa.UnicodeText(), nullable=True),
        sa.Column("error", sa.Unicode(2000), nullable=True),
        clock(),
        clock("finished_at", True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'proposed', 'failed')",
            name="ck_run_setup_request_status",
        ),
    )
    op.create_index(
        "ix_run_setup_requests_board_created", "run_setup_requests", ["board_id", "created_at"]
    )
    # One active request per board, even if two API instances race.
    op.create_index(
        "uq_active_run_setup_request",
        "run_setup_requests",
        ["board_id"],
        unique=True,
        mssql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "run_setup_dispatch_intents",
        ident("request_id", "run_setup_requests.id", primary=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        clock("next_attempt"),
        clock("lease_until", True),
        ident("lease_token", nullable=True),
        sa.Column("last_error", sa.Unicode(2000), nullable=True),
    )
    op.create_index("ix_run_setup_dispatch_intents_state", "run_setup_dispatch_intents", ["state"])
    op.create_index(
        "ix_run_setup_dispatch_intents_next_attempt",
        "run_setup_dispatch_intents",
        ["next_attempt"],
    )


def downgrade():
    op.drop_table("run_setup_dispatch_intents")
    op.drop_table("run_setup_requests")
