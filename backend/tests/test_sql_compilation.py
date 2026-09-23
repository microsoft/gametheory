import re
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import update
from sqlalchemy.dialects import mssql

from gametheory.config import get_settings
from gametheory.persistence import Base, PreparationBoard, Scenario


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
        assert "CREATE TABLE boards" in script
        assert "CREATE TABLE board_previews" in script
        assert "CREATE TABLE connection_configurations" in script
        assert "CREATE TABLE approval_revocations" in script
        assert "ck_approval_execution_disabled" in script
        assert "ck_approval_preparation_only" in script
        assert "expires_at DATETIME2(6)" in script
        assert "REFERENCES revisions (scenario_id, version)" in script
        assert "FOREIGN KEY" in script
        assert "CREATE TABLE run_setup_requests" in script
        assert "CREATE TABLE run_setup_dispatch_intents" in script
        assert "ck_run_setup_request_status" in script
        assert "prompt NVARCHAR(4000) NOT NULL" in script
        assert (
            "CREATE UNIQUE INDEX uq_active_run_setup_request ON run_setup_requests (board_id) "
            "WHERE status IN ('queued', 'running')"
        ) in script
        assert "REFERENCES board_previews (id)" in script
        assert "INSERT INTO alembic_version" in script
        assert all(int(size) <= 4000 for size in re.findall(r"NVARCHAR\((\d+)\)", script.upper()))
    finally:
        get_settings.cache_clear()


def test_long_unicode_columns_compile_to_nvarchar_max():
    dialect = mssql.dialect(deprecate_large_types=True)
    for table, name in (
        ("comments", "body"),
        ("planning_requests", "prompt"),
        ("boards", "draft"),
        ("board_origins", "scenario"),
        ("board_origins", "assets"),
        ("connection_configurations", "snapshot"),
        ("board_previews", "manifest"),
        ("run_setup_requests", "context"),
        ("run_setup_requests", "suggestion"),
    ):
        column = Base.metadata.tables[table].c[name]
        assert column.type.compile(dialect=dialect).upper() == "NVARCHAR(MAX)"


def test_board_conditional_save_compiles_to_sql_server_output():
    query = (
        update(PreparationBoard)
        .where(PreparationBoard.id == "board", PreparationBoard.version == 4)
        .values(version=5)
        .returning(PreparationBoard.id)
    )
    statement = str(query.compile(dialect=mssql.dialect()))
    assert "OUTPUT inserted.id" in statement
    assert "boards.version =" in statement
