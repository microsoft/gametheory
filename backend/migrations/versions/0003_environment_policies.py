"""Versioned environment execution policies; existing targets remain inactive."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mssql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "environment_policies",
        sa.Column(
            "environment_id", sa.String(36), sa.ForeignKey("environments.id"), primary_key=True
        ),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("classification", sa.String(16), nullable=False),
        sa.Column("execution_enabled", sa.Boolean(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        sa.Column("actor", sa.String(36), nullable=False),
        sa.Column("created_at", mssql.DATETIME2(precision=6), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_environment_policy_version"),
        sa.CheckConstraint(
            "classification IN ('unknown', 'nonproduction', 'production')",
            name="ck_environment_policy_classification",
        ),
        sa.CheckConstraint(
            "classification <> 'production' OR approval_required = 1",
            name="ck_production_requires_approval",
        ),
        sa.CheckConstraint(
            "classification <> 'unknown' OR execution_enabled = 0",
            name="ck_unclassified_execution_disabled",
        ),
    )
    op.execute(
        """INSERT INTO environment_policies
        (environment_id, version, classification, execution_enabled, approval_required, actor, created_at)
        SELECT id, 1, 'unknown', 0, 1, '00000000-0000-0000-0000-000000000000', SYSUTCDATETIME()
        FROM environments"""
    )


def downgrade():
    op.drop_table("environment_policies")
