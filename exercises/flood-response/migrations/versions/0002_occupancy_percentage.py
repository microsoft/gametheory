"""Expose numeric occupancy percentages without changing records or retained evidence."""

from pathlib import Path

from alembic import op

revision = "0002_occupancy_percentage"
down_revision = "0001_flood_lab"
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "database"


def upgrade():
    connection = op.get_bind()
    for filename in ("read-occupancy-v2.sql", "update-occupancy-v2.sql"):
        connection.exec_driver_sql((SQL / filename).read_text(encoding="utf-8"))


def downgrade():
    raise RuntimeError("Procedure contract downgrades require coordinated catalog review.")
