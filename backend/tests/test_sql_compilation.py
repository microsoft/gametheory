import re
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import update
from sqlalchemy.dialects import mssql

from gametheory.config import get_settings
from gametheory.persistence import Base, Scenario


def test_conditional_update_uses_sql_server_output():
    query = (
        update(Scenario)
        .where(
            Scenario.id == "id",
            Scenario.version == 4,
        )
        .values(version=5)
        .returning(Scenario.id)
    )
    statement = str(query.compile(dialect=mssql.dialect()))
    assert "OUTPUT inserted.id" in statement
    assert "scenarios.version =" in statement


def test_frozen_migration_compiles_to_sql_server_ddl(monkeypatch):
    monkeypatch.setenv("GT_SQL_URL", "mssql+pyodbc://localhost/gametheory_test")
    get_settings.cache_clear()
    try:
        output = StringIO()
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"), output_buffer=output)
        command.upgrade(config, "head", sql=True)
        script = output.getvalue()
        assert "CREATE TABLE planning_requests" in script
        assert "CREATE TABLE revisions" in script
        assert "CREATE TABLE dispatch_intents" in script
        assert "FOREIGN KEY" in script
        assert "INSERT INTO alembic_version" in script
        assert all(int(size) <= 4000 for size in re.findall(r"NVARCHAR\((\d+)\)", script.upper()))
    finally:
        get_settings.cache_clear()


def test_long_unicode_columns_compile_to_nvarchar_max():
    dialect = mssql.dialect(deprecate_large_types=True)
    for table, name in (("comments", "body"), ("planning_requests", "prompt")):
        column = Base.metadata.tables[table].c[name]
        assert column.type.compile(dialect=dialect).upper() == "NVARCHAR(MAX)"
