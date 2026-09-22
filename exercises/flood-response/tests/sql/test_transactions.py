from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, sessionmaker

from flood_lab.api import create_app, service
from flood_lab.auth import Actor, current_actor
from flood_lab.config import Settings
from flood_lab.contracts import AcknowledgeRequest, AllocateRequest, CreateRequest, OperationError
from flood_lab.database import sessions, utc_now
from flood_lab.models import Event, Receipt
from flood_lab.service import LabService

pytestmark = pytest.mark.sql


def ack_body(lab, key="TEST-ONLY-ACK"):
    record = lab.service.request(lab.participant, lab.run_id, lab.request_id)
    return AcknowledgeRequest(idempotency_key=key, expected_version=record.record_version)


def create_body(lab, **changes):
    with sessions(lab.engine)() as session:
        now = utc_now(session)
    return CreateRequest(
        **{
            "idempotency_key": "TEST-ONLY-CREATE",
            "shelter_id": lab.shelter_id,
            "resource_type": "cots",
            "quantity_requested": 24,
            "summary": "TEST ONLY synthetic request",
            "needed_by": now + timedelta(minutes=20),
            **changes,
        }
    )


def test_durable_receipt_replay_precedes_current_version_after_commit(sql_lab):
    lab = sql_lab
    body = ack_body(lab)
    original = lab.service.acknowledge(lab.participant, lab.run_id, lab.request_id, body, uuid4())
    # TEST ONLY lost-response reconciliation: discard the service, change the record, then retry.
    current = lab.service.request(lab.participant, lab.run_id, lab.request_id)
    allocation = AllocateRequest(
        idempotency_key="TEST-ONLY-ALLOCATE",
        expected_version=current.record_version,
        quantity=current.quantity_requested,
        available_at=current.created_at,
    )
    lab.service.allocate(lab.participant, lab.run_id, lab.request_id, allocation, uuid4())
    fresh_service = LabService(sessions(lab.engine), lab.service.database_name)
    replay = fresh_service.acknowledge(lab.participant, lab.run_id, lab.request_id, body, uuid4())
    assert replay == original
    assert fresh_service.request(lab.participant, lab.run_id, lab.request_id).status == "fulfilled"
    with sessions(lab.engine)() as session:
        count = session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.run_id == lab.run_id, Event.operation == "request.acknowledge")
        )
        assert count == 1
        assert (
            session.scalar(
                select(func.count()).select_from(Receipt).where(Receipt.run_id == lab.run_id)
            )
            == 2
        )


def test_concurrent_duplicate_key_commits_one_receipt_and_event(sql_lab):
    lab, body = sql_lab, ack_body(sql_lab)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: lab.service.acknowledge(
                    lab.participant, lab.run_id, lab.request_id, body, uuid4()
                ),
                range(2),
            )
        )
    assert results[0] == results[1]
    assert results[0].body["outcome"] == "succeeded"
    with sessions(lab.engine)() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Receipt)
                .where(Receipt.run_id == lab.run_id, Receipt.operation == "request.acknowledge")
            )
            == 1
        )


def test_competing_expected_versions_cannot_both_mutate(sql_lab):
    lab = sql_lab
    version = ack_body(lab).expected_version
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda key: lab.service.acknowledge(
                    lab.participant,
                    lab.run_id,
                    lab.request_id,
                    AcknowledgeRequest(idempotency_key=key, expected_version=version),
                    uuid4(),
                ),
                ("TEST-ONLY-A", "TEST-ONLY-B"),
            )
        )
    assert sorted(response.status for response in results) == [200, 409]
    assert {response.body["outcome"] for response in results} == {"succeeded", "rejected"}


def test_changed_payload_is_conflict_and_actor_scope_is_independent(sql_lab):
    lab = sql_lab
    body = create_body(lab)
    first = lab.service.create_request(lab.api_actor, lab.run_id, body, uuid4())
    assert first.status == 201
    assert lab.service.create_request(lab.api_actor, lab.run_id, body, uuid4()) == first
    with pytest.raises(OperationError) as error:
        lab.service.create_request(
            lab.api_actor, lab.run_id, body.model_copy(update={"quantity_requested": 25}), uuid4()
        )
    assert error.value.code == "idempotency_conflict"
    another = Actor(lab.api_actor.tenant_id, uuid4(), "service")
    lab.operator.grant(
        lab.run_id,
        another.tenant_id,
        another.object_id,
        another.kind,
        "api",
        lab.expires_at,
        apply=True,
    )
    second = lab.service.create_request(another, lab.run_id, body, uuid4())
    assert second.body["request_id"] != first.body["request_id"]


def test_actor_roles_run_and_record_relationships_are_enforced(sql_lab):
    lab = sql_lab
    body = ack_body(lab)
    for actor in (lab.api_actor, lab.observer):
        with pytest.raises(OperationError) as error:
            lab.service.acknowledge(actor, lab.run_id, lab.request_id, body, uuid4())
        assert error.value.status == 403
    with pytest.raises(OperationError):
        lab.service.create_request(lab.participant, lab.run_id, create_body(lab), uuid4())
    with pytest.raises(OperationError) as error:
        lab.service.request(
            Actor(lab.participant.tenant_id, uuid4(), "user"), lab.run_id, lab.request_id
        )
    assert error.value.status == 404
    other_manifest = lab.operator.seed("test-only-" + uuid4().hex, apply=True)["manifest"]
    other_request = UUID(other_manifest["requests"][0]["id"])
    with pytest.raises(OperationError):
        lab.service.request(lab.participant, lab.run_id, other_request)
    response = lab.service.acknowledge(lab.participant, lab.run_id, other_request, body, uuid4())
    assert response.status == 404
    response = lab.service.create_request(
        lab.api_actor,
        lab.run_id,
        create_body(lab, shelter_id=other_manifest["shelters"][0]["id"]),
        uuid4(),
    )
    assert response.status == 404


def test_grant_revocation_prevents_read_and_replay(sql_lab):
    lab = sql_lab
    body = ack_body(lab)
    lab.service.acknowledge(lab.participant, lab.run_id, lab.request_id, body, uuid4())
    lab.operator.grant(
        lab.run_id,
        lab.participant.tenant_id,
        lab.participant.object_id,
        "user",
        "participant",
        lab.expires_at,
        revoke=True,
        apply=True,
    )
    with pytest.raises(OperationError):
        lab.service.acknowledge(lab.participant, lab.run_id, lab.request_id, body, uuid4())
    assert lab.service.list_runs(lab.participant, 50, 0).items == []


def test_receipt_failure_rolls_back_mutation_and_event(sql_lab):
    lab = sql_lab

    class TestOnlyFailingSession(Session):
        pass

    def fail_receipt(session, flush_context, instances):
        if any(isinstance(item, Receipt) for item in session.new):
            raise RuntimeError("TEST ONLY receipt persistence failure")

    event.listen(TestOnlyFailingSession, "before_flush", fail_receipt)
    failing = LabService(
        sessionmaker(lab.engine, class_=TestOnlyFailingSession, expire_on_commit=False),
        lab.service.database_name,
    )
    with pytest.raises(RuntimeError):
        failing.acknowledge(lab.participant, lab.run_id, lab.request_id, ack_body(lab), uuid4())
    assert lab.service.request(lab.participant, lab.run_id, lab.request_id).status == "open"
    with sessions(lab.engine)() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.run_id == lab.run_id, Event.operation == "request.acknowledge")
            )
            == 0
        )


def test_quantity_adequacy_and_bounded_event_pages(sql_lab):
    lab = sql_lab
    lab.service.acknowledge(lab.participant, lab.run_id, lab.request_id, ack_body(lab), uuid4())
    for index, quantity in enumerate((15, 25)):
        record = lab.service.request(lab.participant, lab.run_id, lab.request_id)
        response = lab.service.allocate(
            lab.participant,
            lab.run_id,
            lab.request_id,
            AllocateRequest(
                idempotency_key=f"TEST-ONLY-ALLOCATION-{index}",
                expected_version=record.record_version,
                quantity=quantity,
                available_at=record.created_at,
            ),
            uuid4(),
        )
        assert response.status == 200
    assert lab.service.request(lab.participant, lab.run_id, lab.request_id).quantity_allocated == 40
    page = lab.service.events(lab.observer, lab.run_id, 2, 0, None)
    assert len(page.items) == 2 and page.next_after is not None
    following = lab.service.events(lab.observer, lab.run_id, 2, page.next_after, None)
    assert not {item.durable_event_id for item in page.items}.intersection(
        item.durable_event_id for item in following.items
    )


def test_real_sql_http_headers_reconcile_after_later_record_changes(sql_lab):
    lab = sql_lab
    app = create_app(Settings())
    # TEST ONLY identity override; the data path is the explicitly selected real SQL database.
    app.dependency_overrides[current_actor] = lambda: lab.participant
    app.dependency_overrides[service] = lambda: lab.service
    path = f"/v1/runs/{lab.run_id}/requests/{lab.request_id}"
    with TestClient(app) as client:
        initial = client.get(path)
        assert initial.status_code == 200
        assert initial.headers["etag"] == f'"{initial.json()["record_version"]}"'
        headers = {
            "If-Match": initial.headers["etag"],
            "Idempotency-Key": "TEST-ONLY-HTTP-ACK",
        }
        acknowledged = client.post(path + "/acknowledge", json={}, headers=headers)
        assert acknowledged.status_code == 200
        allocation = client.post(
            path + "/allocate",
            json={"quantity": 40, "available_at": initial.json()["created_at"]},
            headers={
                "If-Match": acknowledged.headers["etag"],
                "Idempotency-Key": "TEST-ONLY-HTTP-ALLOCATION",
            },
        )
        assert allocation.status_code == 200
        replay = client.post(path + "/acknowledge", json={}, headers=headers)
        assert replay.json() == acknowledged.json()
        assert replay.headers["etag"] == acknowledged.headers["etag"]
        conflict = client.post(
            path + "/acknowledge",
            json={},
            headers=headers | {"If-Match": allocation.headers["etag"]},
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "idempotency_conflict"
