from alembic import context
from sqlalchemy import text

from flood_lab.config import Settings, SetupRequired
from flood_lab.database import make_engine

config = context.config


def migrate(connection):
    expected = config.attributes.get("database_name") or Settings().database_name
    actual = connection.scalar(text("SELECT DB_NAME()"))
    if actual != expected or not expected.startswith("flood_lab_"):
        raise SetupRequired("Refusing migration outside the selected dedicated lab database.")
    marker_exists = connection.scalar(text("SELECT OBJECT_ID('flood.database_identity', 'U')"))
    if marker_exists is None:
        foreign_tables = connection.scalar(
            text(
                "SELECT COUNT(*) FROM sys.tables "
                "WHERE is_ms_shipped=0 AND name <> 'flood_alembic_version'"
            )
        )
        if foreign_tables:
            raise SetupRequired("Refusing to initialize a database containing unrelated tables.")
    else:
        marker = connection.execute(
            text("SELECT contract_version, database_name FROM flood.database_identity WHERE id=1")
        ).one_or_none()
        if marker is None or marker[0] != "flood-lab/v1" or marker[1] != expected:
            raise SetupRequired("Dedicated lab database identity does not match.")
    if not connection.scalar(text("SELECT SCHEMA_ID('flood')")):
        connection.execute(text("CREATE SCHEMA flood AUTHORIZATION dbo"))
    connection.commit()
    context.configure(
        connection=connection,
        version_table="flood_alembic_version",
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise SetupRequired("Use the operator migrate preview; offline SQL cannot verify isolation.")
elif config.attributes.get("connection") is not None:
    migrate(config.attributes["connection"])
else:
    settings = Settings()
    settings.database(operator=True)
    engine = make_engine(settings.operator_database_url, settings.database_name)
    with engine.connect() as connection:
        migrate(connection)
