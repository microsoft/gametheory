"""Offline SQL contract checks, not a substitute for executing the real-SQL suite."""

import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateIndex, CreateTable

from flood_lab.auth import Actor
from flood_lab.database import locked
from flood_lab.models import Base, Receipt, ResourceRequest
from flood_lab.service import LabService

ROOT = Path(__file__).resolve().parents[2]


def test_migration_snapshot_has_all_declared_tables_and_columns():
    sql = (ROOT / "migrations/database/schema-v1.sql").read_text()
    for table in Base.metadata.tables.values():
        match = re.search(rf"CREATE TABLE flood\.{table.name} \((.*?)\n\);", sql, re.DOTALL)
        assert match, table.name
        for column in table.columns:
            assert re.search(rf"^\s+{column.name}\s", match[1], re.MULTILINE), column.name
    assert "uq_receipt_identity UNIQUE(actor_key,run_id,operation,idempotency_key)" in sql
    assert "COLLATE Latin1_General_100_BIN2" in sql


def test_mssql_queries_bind_values_and_use_real_lock_hints():
    record_id = uuid4()
    query = locked(select(ResourceRequest).where(ResourceRequest.id == record_id))
    compiled = query.compile(dialect=mssql.dialect())
    assert "WITH (UPDLOCK, HOLDLOCK)" in str(compiled)
    assert str(record_id) not in str(compiled)
    assert record_id in compiled.params.values()
    identity = {
        column.name
        for constraint in Receipt.__table__.constraints
        if constraint.name == "uq_receipt_identity"
        for column in constraint.columns
    }
    assert identity == {"actor_key", "run_id", "operation", "idempotency_key"}


def test_run_list_uses_sql_server_bit_comparison(monkeypatch):
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    session.execute.return_value.all.return_value = []
    now = datetime(2026, 9, 22, tzinfo=UTC)
    monkeypatch.setattr("flood_lab.service.verify_database", lambda *_: None)
    monkeypatch.setattr("flood_lab.service.utc_now", lambda _: now)
    actor = Actor(uuid4(), uuid4(), "user")

    assert LabService(factory, "flood_lab_test_compile").list_runs(actor, 50, 0).items == []

    query = session.execute.call_args.args[0]
    compiled = query.compile(dialect=mssql.dialect())
    assert "flood.run_grants.active = 1" in str(compiled)
    assert " IS 1" not in str(compiled)
    assert {actor.tenant_id, actor.object_id, actor.kind, now} <= set(compiled.params.values())


def test_every_table_and_index_compiles_for_sql_server():
    dialect = mssql.dialect()
    compiled_tables = []
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE flood.{table.name}" in ddl
        compiled_tables.append(ddl)
        for index in table.indexes:
            assert "CREATE INDEX" in str(CreateIndex(index).compile(dialect=dialect))
    combined = "\n".join(compiled_tables)
    assert "DATETIME2(6)" in combined
    assert "VARBINARY(85)" in combined
    assert "UNIQUEIDENTIFIER" in combined
    assert "IDENTITY" in combined
    assert "FOREIGN KEY(run_id, shelter_id) REFERENCES flood.shelters (run_id, id)" in combined
    assert "FOREIGN KEY(run_id, request_id) REFERENCES flood.requests (run_id, id)" in combined


def test_procedure_names_and_replay_order_are_frozen():
    for version in ("v1", "v2"):
        update = (ROOT / f"migrations/database/update-occupancy-{version}.sql").read_text()
        assert "PROCEDURE flood.UpdateOccupancy" in update
        assert "sys.sp_getapplock" in update
        assert update.index("IF @Result IS NOT NULL") < update.index("SET @Code='version_conflict'")
        assert "principal_sid=@Sid" in update
        assert "COMMIT TRANSACTION" in update
        assert "sp_executesql" not in update


def test_percentage_migration_is_additive_and_missing_evidence_is_not_synthesized():
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_current_head() == "0002_occupancy_percentage"
    assert scripts.get_revision("head").down_revision == "0001_flood_lab"
    read = (ROOT / "migrations/database/read-occupancy-v2.sql").read_text()
    assert "CREATE OR ALTER PROCEDURE flood.ReadOccupancy" in read
    assert "CONVERT(float, s.occupancy) * 100.0 / s.capacity AS occupancy_percent" in read
    assert "e.id AS durable_event_id" in read
    assert "CONVERT(datetimeoffset(6), e.committed_at) AS committed_at" in read
    assert "OUTER APPLY" in read
    assert "COALESCE" not in read
    update = (ROOT / "migrations/database/update-occupancy-v2.sql").read_text()
    assert "occupancy_percent float" in update
    assert "END AS occupancy_percent" in update
    assert "UPDATE flood.receipts" not in update
