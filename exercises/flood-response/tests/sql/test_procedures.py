from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from flood_lab.assessment import detection, evidence_from_result
from flood_lab.database import sessions
from flood_lab.models import Event, Shelter

pytestmark = pytest.mark.sql

PROCEDURE = text(
    "EXEC flood.UpdateOccupancy @run_id=:run_id, @shelter_id=:shelter_id, "
    "@occupancy=:occupancy, @expected_version=:expected_version, @idempotency_key=:idempotency_key"
)
READ = text("EXEC flood.ReadOccupancy @run_id=:run_id, @shelter_id=:shelter_id")


def sql_grant_current(lab):
    with lab.engine.connect() as connection:
        principal = connection.scalar(text("SELECT USER_NAME()"))
    lab.operator.sql_grant(lab.run_id, principal, "inject", lab.expires_at, apply=True)


def inject(lab, params):
    with lab.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        return dict(connection.execute(PROCEDURE, params).mappings().one())


def read_occupancy(lab):
    with lab.engine.connect() as connection:
        return dict(
            connection.execute(READ, {"run_id": str(lab.run_id), "shelter_id": str(lab.shelter_id)})
            .mappings()
            .one()
        )


def test_sql_procedure_receipt_replay_and_concurrent_injection(sql_lab):
    lab = sql_lab
    sql_grant_current(lab)
    with lab.engine.connect() as connection:
        initial = dict(
            connection.execute(
                READ,
                {
                    "run_id": str(lab.run_id),
                    "shelter_id": str(lab.shelter_id),
                },
            )
            .mappings()
            .one()
        )
    params = {
        "run_id": str(lab.run_id),
        "shelter_id": str(lab.shelter_id),
        "occupancy": 108,
        "expected_version": initial["record_version"],
        "idempotency_key": "TEST-ONLY-SQL-IDEMPOTENCY",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: inject(lab, params), range(2)))
    assert results[0] == results[1]
    assert results[0]["outcome"] == "succeeded"
    assert results[0]["occupancy_percent"] == 90.0
    latest = inject(
        lab,
        {
            **params,
            "expected_version": results[0]["record_version"],
            "idempotency_key": "TEST-ONLY-SQL-SECOND",
            "occupancy": 110,
        },
    )
    assert latest["occupancy"] == 110
    assert latest["occupancy_percent"] == pytest.approx(11000 / 120, rel=1e-12)
    assert inject(lab, params) == results[0]
    with pytest.raises(DBAPIError):
        inject(lab, {**params, "occupancy": 109})


def test_percentage_supports_exact_85_and_strictly_above_without_client_math(sql_lab):
    lab = sql_lab
    sql_grant_current(lab)
    for occupancy, expected, should_trigger in ((102, 85.0, False), (103, 10300 / 120, True)):
        current = read_occupancy(lab)
        changed = inject(
            lab,
            {
                "run_id": str(lab.run_id),
                "shelter_id": str(lab.shelter_id),
                "occupancy": occupancy,
                "expected_version": current["record_version"],
                "idempotency_key": f"TEST-ONLY-PERCENT-{occupancy}",
            },
        )
        observed = read_occupancy(lab)
        assert changed["outcome"] == "succeeded"
        for result in (changed, observed):
            assert isinstance(result["occupancy_percent"], float)
            assert result["occupancy_percent"] == pytest.approx(expected, rel=1e-12)
            assert (result["occupancy_percent"] > 85) is should_trigger
        assert observed["durable_event_id"] == changed["durable_event_id"]
        assert observed["committed_at"] == changed["committed_at"]


def test_missing_matching_event_stays_absent_and_timing_indeterminate(sql_lab):
    lab = sql_lab
    sql_grant_current(lab)
    with sessions(lab.engine).begin() as session:
        shelter = session.get(Shelter, lab.shelter_id)
        shelter.occupancy, shelter.record_version = 108, uuid4()
        count_before = session.scalar(
            select(func.count()).select_from(Event).where(Event.run_id == lab.run_id)
        )
    observed = read_occupancy(lab)
    assert observed["occupancy_percent"] == 90.0
    assert observed["durable_event_id"] is None
    assert observed["committed_at"] is None
    assert evidence_from_result(observed) is None
    assert detection(evidence_from_result(observed), None).state == "indeterminate"
    with sessions(lab.engine)() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Event).where(Event.run_id == lab.run_id)
            )
            == count_before
        )


def test_procedure_checks_record_run_relation_and_expected_version(sql_lab):
    lab = sql_lab
    sql_grant_current(lab)
    record = lab.manifest["shelters"][0]
    version = '"v1:' + record["record_version"].replace("-", "") + '"'
    params = {
        "run_id": str(lab.run_id),
        "shelter_id": str(lab.shelter_id),
        "occupancy": 108,
        "expected_version": version,
        "idempotency_key": "TEST-ONLY-FIRST",
    }
    assert inject(lab, params)["outcome"] == "succeeded"
    assert (
        inject(lab, {**params, "idempotency_key": "TEST-ONLY-STALE"})["code"] == "version_conflict"
    )
    assert (
        inject(
            lab,
            {
                **params,
                "shelter_id": "11111111-1111-1111-1111-111111111111",
                "idempotency_key": "TEST-ONLY-WRONG-RECORD",
            },
        )["code"]
        == "resource_unavailable"
    )


def test_distinct_database_roles_enforce_least_privilege(sql_lab):
    lab = sql_lab
    with lab.engine.begin() as connection:
        connection.execute(
            text("""
            IF USER_ID('flood_lab_fixture_observer') IS NULL
            BEGIN
                CREATE USER flood_lab_fixture_observer WITHOUT LOGIN;
                ALTER ROLE flood_observer ADD MEMBER flood_lab_fixture_observer;
            END;
            IF USER_ID('flood_lab_fixture_api') IS NULL
            BEGIN
                CREATE USER flood_lab_fixture_api WITHOUT LOGIN;
                ALTER ROLE flood_api ADD MEMBER flood_lab_fixture_api;
            END;
        """)
        )
    lab.operator.sql_grant(
        lab.run_id, "flood_lab_fixture_observer", "observe", lab.expires_at, apply=True
    )
    with lab.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("EXECUTE AS USER='flood_lab_fixture_observer'"))
        try:
            assert (
                connection.execute(
                    READ,
                    {
                        "run_id": str(lab.run_id),
                        "shelter_id": str(lab.shelter_id),
                    },
                )
                .mappings()
                .one()["occupancy"]
                == 84
            )
            assert (
                connection.scalar(
                    text("SELECT HAS_PERMS_BY_NAME('flood.UpdateOccupancy','OBJECT','EXECUTE')")
                )
                == 0
            )
            assert (
                connection.scalar(
                    text("SELECT HAS_PERMS_BY_NAME('flood.shelters','OBJECT','UPDATE')")
                )
                == 0
            )
        finally:
            connection.execute(text("REVERT"))
        connection.execute(text("EXECUTE AS USER='flood_lab_fixture_api'"))
        try:
            for table in ("flood.run_grants", "flood.shelters", "flood.events", "flood.receipts"):
                assert (
                    connection.scalar(
                        text("SELECT HAS_PERMS_BY_NAME(:name,'OBJECT','UPDATE')"), {"name": table}
                    )
                    == 0
                )
            assert (
                connection.scalar(
                    text("SELECT HAS_PERMS_BY_NAME('flood.UpdateOccupancy','OBJECT','EXECUTE')")
                )
                == 0
            )
        finally:
            connection.execute(text("REVERT"))
