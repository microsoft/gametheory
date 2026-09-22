"""Independent flood-lab/v1 database. SQL files are immutable migration artifacts."""

from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0001_flood_lab"
down_revision = None
branch_labels = None
depends_on = None

SQL = Path(__file__).resolve().parents[1] / "database"


def upgrade():
    connection = op.get_bind()
    for filename in (
        "schema-v1.sql",
        "read-occupancy-v1.sql",
        "update-occupancy-v1.sql",
        "permissions-v1.sql",
    ):
        source = (SQL / filename).read_text(encoding="utf-8")
        for batch in source.split("\nGO\n"):
            if batch.strip():
                connection.exec_driver_sql(batch)
    database_name = connection.scalar(text("SELECT DB_NAME()"))
    connection.execute(
        text(
            "INSERT INTO flood.database_identity(id, contract_version, database_name, purpose) "
            "VALUES (1, 'flood-lab/v1', :name, :purpose)"
        ),
        {
            "name": database_name,
            "purpose": (
                "disposable-tests" if database_name.startswith("flood_lab_test_") else "exercise"
            ),
        },
    )


def downgrade():
    raise RuntimeError(
        "Evidence is retained. Use scoped operator recovery, not a destructive migration downgrade."
    )
