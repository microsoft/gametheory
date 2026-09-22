from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from flood_lab.assessment import (
    Evidence,
    acknowledgement,
    allocation,
    capacity_trigger,
    detection,
    evidence_from_result,
)
from flood_lab.config import SetupRequired, database_target
from flood_lab.contracts import (
    AllocateRequest,
    CreateRequest,
    OperationError,
    parse_version,
    version_tag,
    version_value,
)
from flood_lab.service import fingerprint


def evidence(seconds=0, quantity=None):
    return Evidence(
        uuid4(), datetime(2030, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds), quantity
    )


def test_strong_opaque_etag_roundtrip():
    version = uuid4()
    assert parse_version(version_tag(version)) == version
    for value in ("1", 'W/"v1:' + version.hex + '"', "*", '"v1:' + version.hex.upper() + '"'):
        with pytest.raises(OperationError):
            parse_version(value)
    with pytest.raises(OperationError) as error:
        parse_version(None)
    assert error.value.status == 428


def test_fingerprint_is_canonical_and_includes_preconditions():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
    assert fingerprint({"expected_version": "old"}) != fingerprint({"expected_version": "new"})
    with pytest.raises(ValueError):
        fingerprint({"quantity": float("nan")})


@pytest.mark.parametrize(
    "url,name",
    [
        ("sqlite://", "flood_lab_test_case"),
        ("mssql+pyodbc://host/gametheory", "gametheory"),
        ("mssql+pyodbc://host/app", "flood_lab_test_case"),
        (
            "mssql+pyodbc://host/flood_lab_case?driver=ODBC+Driver+18+for+SQL+Server",
            "flood_lab_case",
        ),
    ],
)
def test_database_isolation_fails_closed(url, name):
    with pytest.raises(SetupRequired):
        database_target(url, name)


def test_database_accepts_explicit_encrypted_mssql_only():
    url = (
        "mssql+pyodbc://fixture:NOT-A-REAL-SECRET@localhost/flood_lab_case?"
        "driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes"
    )
    assert database_target(url, "flood_lab_case").database == "flood_lab_case"


def test_flat_mutation_input_bounds_and_utc():
    base = {
        "idempotency_key": "test-only-key",
        "shelter_id": str(uuid4()),
        "resource_type": "cots",
        "quantity_requested": 1,
        "summary": "EXERCISE ONLY",
        "needed_by": "2030-01-01T10:00:00Z",
    }
    assert CreateRequest(**base).needed_by.tzinfo == UTC
    for change in (
        {"quantity_requested": True},
        {"quantity_requested": "1"},
        {"quantity_requested": 10001},
        {"summary": "x" * 241},
        {"needed_by": "2030-01-01T10:00:00"},
        {"needed_by": "2030-01-01T10:00:00+01:00"},
        {"role": "operator"},
        {"idempotency_key": "invalid key"},
    ):
        with pytest.raises(ValidationError):
            CreateRequest(**(base | change))
    with pytest.raises(ValidationError):
        AllocateRequest(
            idempotency_key="test-only",
            expected_version=version_value(uuid4()),
            quantity=1.5,
            available_at="2030-01-01T10:00:00Z",
        )


def test_demonstration_clock_boundaries_and_missing_evidence():
    assert not capacity_trigger(85, 100)
    assert capacity_trigger(86, 100)
    assert detection(evidence(), evidence(120)).state == "met"
    assert detection(evidence(), evidence(121)).state == "not_met"
    assert acknowledgement(evidence(), evidence(600)).state == "met"
    assert acknowledgement(evidence(), evidence(601)).state == "not_met"
    assert acknowledgement(evidence(), None).state == "indeterminate"
    assert detection(evidence(120), evidence()).state == "indeterminate"


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"durable_event_id": None, "committed_at": None},
        {"durable_event_id": "11111111-1111-4111-8111-111111111111"},
        {"committed_at": "2030-01-01T00:00:00Z"},
        {"durable_event_id": "invalid", "committed_at": "2030-01-01T00:00:00Z"},
        {
            "durable_event_id": "11111111-1111-4111-8111-111111111111",
            "committed_at": "2030-01-01T00:00:00",
        },
    ],
)
def test_missing_read_evidence_never_creates_an_event_or_start_time(result):
    source = evidence_from_result(result)
    assert source is None
    assert detection(source, evidence(120)).state == "indeterminate"


def test_complete_read_evidence_preserves_original_id_and_utc_timestamp():
    original = evidence()
    parsed = evidence_from_result(
        {
            "durable_event_id": str(original.durable_event_id),
            "committed_at": original.committed_at.isoformat(),
        }
    )
    assert parsed == original


def test_adequate_allocation_needs_complete_distinct_durable_evidence():
    created = evidence()
    events = [evidence(600, 15), evidence(1200, 25)]
    assert allocation(created, events, 40, evidence_complete=True).state == "met"
    assert (
        allocation(
            created,
            events,
            41,
            evidence_complete=True,
            observed_through=evidence(1200).committed_at,
        ).state
        == "not_met"
    )
    assert allocation(created, events, 40, evidence_complete=False).state == "indeterminate"
    assert allocation(created, None, 40, evidence_complete=True).state == "indeterminate"
    assert allocation(created, [], 40, evidence_complete=True).state == "indeterminate"
    assert (
        allocation(created, [events[0], events[0]], 30, evidence_complete=True).state
        == "indeterminate"
    )
    assert (
        allocation(
            created,
            [evidence(1201, 40)],
            40,
            evidence_complete=True,
            observed_through=evidence(1201).committed_at,
        ).state
        == "not_met"
    )
    assert allocation(created, events, 41, evidence_complete=True).state == "indeterminate"
