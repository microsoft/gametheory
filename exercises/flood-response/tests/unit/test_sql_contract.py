"""Offline SQL contract checks, not a substitute for executing the real-SQL suite."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateIndex, CreateTable

from flood_lab.auth import Actor
from flood_lab.database import RUN_LOCKS, locked
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


def test_milestones_share_the_writer_lock_before_one_clock_read(monkeypatch):
    calls = []
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    run_id, request_id = uuid4(), uuid4()
    created = datetime(2030, 1, 1, tzinfo=UTC)
    record = SimpleNamespace(
        id=request_id,
        run_id=run_id,
        record_version=uuid4(),
        status="open",
        quantity_requested=4,
        quantity_allocated=0,
        created_at=created,
        acknowledged_at=None,
    )
    monkeypatch.setattr("flood_lab.service.verify_database", lambda *_: calls.append("verify"))
    monkeypatch.setattr(LabService, "_authorize", lambda *_: calls.append("authorize"))
    monkeypatch.setattr(
        "flood_lab.service.lock_run", lambda _, run, mode="Exclusive": calls.append((mode, run))
    )
    monkeypatch.setattr(
        "flood_lab.service.utc_now", lambda _: calls.append("clock") or created + timedelta(1)
    )
    monkeypatch.setattr(LabService, "_request", lambda *_: calls.append("record") or record)
    queries = []
    session.scalars.side_effect = lambda query: queries.append(query) or []
    actor = Actor(uuid4(), uuid4(), "user")

    view = LabService(factory, "flood_lab_test_compile").milestones(
        actor, run_id, request_id, 600, 1200
    )

    assert calls == ["verify", "authorize", ("Shared", run_id), "clock", "record"]
    assert view.as_of == created + timedelta(1)
    assert view.acknowledgement_deadline == created + timedelta(seconds=600)
    assert (view.acknowledged_on_time, view.allocated_on_time) == (None, None)
    events, allocations = (query.compile(dialect=mssql.dialect()) for query in queries)
    assert "ORDER BY flood.events.sequence" in str(events)
    bound = list(events.params.values())
    assert run_id in bound and request_id in bound and "succeeded" in bound
    assert ["request.create", "request.acknowledge", "request.allocate"] in bound
    assert str(request_id) not in str(events)
    assert "flood.allocations.request_id" in str(allocations)
    assert "@LockMode='Shared', @LockOwner='Transaction'" in str(RUN_LOCKS["Shared"])
    assert "@LockMode='Exclusive', @LockOwner='Transaction'" in str(RUN_LOCKS["Exclusive"])
