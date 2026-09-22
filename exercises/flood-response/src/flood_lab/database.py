from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from flood_lab import CONTRACT_VERSION
from flood_lab.config import SetupRequired, database_target
from flood_lab.models import DatabaseIdentity


def make_engine(url: str, expected_name: str) -> Engine:
    return create_engine(
        database_target(url, expected_name),
        pool_pre_ping=True,
        hide_parameters=True,
        echo=False,
        connect_args={"timeout": 10},
    )


def sessions(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def verify_database(session: Session, expected_name: str) -> None:
    actual = session.scalar(text("SELECT DB_NAME()"))
    marker = session.get(DatabaseIdentity, 1)
    if (
        actual != expected_name
        or marker is None
        or marker.contract_version != CONTRACT_VERSION
        or marker.database_name != expected_name
        or marker.purpose not in {"exercise", "disposable-tests"}
    ):
        raise SetupRequired("The selected database is not an initialized dedicated lab database.")


def utc_now(session: Session) -> datetime:
    value = session.scalar(text("SELECT SYSUTCDATETIME()"))
    if not isinstance(value, datetime):
        raise SetupRequired("SQL Server UTC time is unavailable.")
    return value.replace(tzinfo=UTC)


def lock_run(session: Session, run_id: UUID) -> None:
    # Every sanctioned writer takes the same transaction-owned lock, including SQL injectors.
    result = session.execute(
        text(
            "DECLARE @rc int; EXEC @rc = sys.sp_getapplock "
            "@Resource=:resource, @LockMode='Exclusive', @LockOwner='Transaction', "
            "@LockTimeout=5000; SELECT @rc;"
        ),
        {"resource": f"flood:run:{str(run_id).lower()}"},
    ).scalar_one()
    if result < 0:
        raise TimeoutError("Run is busy; reconcile using the same idempotency key.")


def locked(statement):  # type: ignore[no-untyped-def]
    return statement.with_hint(
        statement.column_descriptions[0]["entity"], "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql"
    )


def operator_identity(session: Session) -> str:
    result = session.execute(
        text("SELECT USER_NAME(), IS_ROLEMEMBER('flood_operator'), IS_ROLEMEMBER('db_owner')")
    ).one()
    if result[1] != 1 and result[2] != 1:
        raise PermissionError("A separate flood_operator database identity is required.")
    return str(result[0])


def principal_sid(session: Session) -> bytes:
    return bytes(
        session.scalar(text("SELECT sid FROM sys.database_principals WHERE principal_id=USER_ID()"))
    )


def database_actor_key(session: Session) -> str:
    return "sql:" + hashlib.sha256(principal_sid(session)).hexdigest()
