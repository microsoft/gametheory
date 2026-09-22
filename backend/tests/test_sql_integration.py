from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from gametheory.api import app
from gametheory.auth import Principal, authenticate
from gametheory.domain import ProposalContent, ScenarioContent
from gametheory.persistence import Asset, Audit, Membership, PlanningRequest, Revision

pytestmark = pytest.mark.integration


def create(client):
    workspace = client.post("/api/workspaces", json={"name": "Resilience"}).json()
    wid = workspace["id"]
    scenario = client.post(f"/api/workspaces/{wid}/scenarios", json={"name": "Flood"}).json()
    return wid, scenario, f"/api/workspaces/{wid}/scenarios/{scenario['id']}"


def test_sql_drafts_conflicts_revisions_and_transactional_audit(sql_client, sql_factory):
    client, _ = sql_client
    wid, scenario, path = create(client)
    body = scenario["content"] | {"title": "Flood response"}
    assert client.put(path, json=body).status_code == 428
    response = client.put(path, json=body, headers={"If-Match": '"1"'})
    assert response.status_code == 200
    assert response.headers["etag"] == '"2"'
    assert client.put(path, json=body, headers={"If-Match": '"1"'}).status_code == 409
    assert client.post(path + "/revisions", headers={"If-Match": '"2"'}).status_code == 201
    assert (
        client.put(
            path, json=body | {"title": "Another title"}, headers={"If-Match": '"2"'}
        ).status_code
        == 200
    )
    assert client.get(path).json()["content"]["title"] == "Another title"
    with sql_factory() as db:
        frozen = db.get(Revision, (scenario["id"], 2))
        assert ScenarioContent.model_validate_json(frozen.content).title == "Flood response"
        events = list(db.scalars(select(Audit).where(Audit.workspace_id == wid)))
        assert len([a for a in events if a.operation == "scenario.saved"]) == 2


def test_maximum_comment_and_prompt_lengths_persist(sql_client, sql_factory):
    client, actor = sql_client
    _, scenario, path = create(client)
    comment = "x" * 5000
    response = client.post(path + "/comments", json={"body": comment, "base_version": 1})
    assert response.status_code == 201, response.text
    assert client.get(path + "/comments").json()[0]["body"] == comment
    request_id = str(uuid4())
    with sql_factory.begin() as db:
        db.add(
            PlanningRequest(
                id=request_id,
                scenario_id=scenario["id"],
                actor=actor.object_id,
                base_version=1,
                prompt="x" * 8000,
                context=ScenarioContent.model_validate(scenario["content"]).model_dump_json(),
            )
        )
    with sql_factory() as db:
        assert db.get(PlanningRequest, request_id).prompt == "x" * 8000


def test_cross_workspace_and_viewer_writes_are_denied(sql_client, sql_factory):
    client, actor = sql_client
    wid, scenario, path = create(client)
    viewer = Principal(actor.tenant, str(uuid4()))
    with sql_factory.begin() as db:
        db.add(Membership(workspace_id=wid, object_id=viewer.object_id, role="viewer"))
    app.dependency_overrides[authenticate] = lambda: viewer
    assert client.get(path).status_code == 200
    assert (
        client.put(path, json=scenario["content"], headers={"If-Match": '"1"'}).status_code == 403
    )
    assert (
        client.post(path + "/comments", json={"body": "change", "base_version": 1}).status_code
        == 403
    )
    outsider = Principal(actor.tenant, str(uuid4()))
    app.dependency_overrides[authenticate] = lambda: outsider
    assert client.get(path).status_code == 404
    assert client.get(f"/api/workspaces/{wid}/assets").status_code == 404
    assert client.get("/api/workspaces").json() == []


def test_stale_proposal_application_rolls_back_its_decision(sql_client, sql_factory):
    client, actor = sql_client
    _, scenario, path = create(client)
    pid = str(uuid4())
    with sql_factory.begin() as db:
        db.add(
            PlanningRequest(
                id=pid,
                scenario_id=scenario["id"],
                actor=actor.object_id,
                base_version=1,
                prompt="Refine",
                context=ScenarioContent(title="Flood").model_dump_json(),
                status="proposed",
                proposal=ProposalContent(
                    summary="Refine",
                    content=ScenarioContent(title="Proposed flood"),
                ).model_dump_json(),
            )
        )
    client.put(
        path, json=scenario["content"] | {"title": "Human edit"}, headers={"If-Match": '"1"'}
    )
    assert (
        client.post(path + f"/planning/{pid}/apply", headers={"If-Match": '"1"'}).status_code == 409
    )
    assert client.get(path).json()["content"]["title"] == "Human edit"
    with sql_factory() as db:
        assert db.get(PlanningRequest, pid).status == "proposed"


def test_asset_failure_never_publishes_a_usable_version(sql_client, sql_factory, monkeypatch):
    client, _ = sql_client
    wid, _, _ = create(client)

    def fail(*_):
        raise HTTPException(503, "Test-only storage failure")

    monkeypatch.setattr("gametheory.api.put_blob", fail)
    response = client.post(
        f"/api/workspaces/{wid}/assets", files={"file": ("data.csv", b"id,value\n1,2", "text/csv")}
    )
    assert response.status_code == 503
    with sql_factory() as db:
        asset = db.scalar(select(Asset).where(Asset.workspace_id == wid))
        assert asset.state == "staged"
        assert client.get(f"/api/workspaces/{wid}/assets/{asset.id}/content").status_code == 404


def test_ready_asset_pins_versions_and_checks_reference_scope(sql_client, monkeypatch):
    client, _ = sql_client
    wid, scenario, path = create(client)
    monkeypatch.setattr("gametheory.api.put_blob", lambda *_: None)
    response = client.post(
        f"/api/workspaces/{wid}/assets", files={"file": ("data.csv", b"a,b", "text/csv")}
    )
    assert response.status_code == 201, response.text
    first = response.json()
    monkeypatch.setattr("gametheory.api.read_blob", lambda _, **kwargs: b"a,b")
    assert client.get(f"/api/workspaces/{wid}/assets/{first['id']}/content").content == b"a,b"
    monkeypatch.setattr("gametheory.api.read_blob", lambda _, **kwargs: b"changed bytes")
    assert client.get(f"/api/workspaces/{wid}/assets/{first['id']}/content").status_code == 409
    second = client.post(
        f"/api/workspaces/{wid}/assets?previous_id={first['id']}",
        files={"file": ("data.csv", b"c,d", "text/csv")},
    ).json()
    assert second["id"] != first["id"]
    assert second["previous_id"] == first["id"]
    content = scenario["content"] | {"asset_ids": [first["id"]]}
    assert client.put(path, json=content, headers={"If-Match": '"1"'}).status_code == 200
    assert client.post(path + "/revisions", headers={"If-Match": '"2"'}).status_code == 201
    other_wid, other, other_path = create(client)
    assert other_wid != wid
    assert (
        client.put(
            other_path,
            json=other["content"] | {"asset_ids": [first["id"]]},
            headers={"If-Match": '"1"'},
        ).status_code
        == 422
    )


def test_metadata_connection_scopes_and_target_labels(sql_client):
    client, _ = sql_client
    wid, scenario, path = create(client)
    eid = client.post("/api/environments", json={"name": "Test"}).json()["id"]
    connection = client.post(
        f"/api/workspaces/{wid}/connections",
        json={
            "name": "Training database",
            "kind": "sql",
            "environment_id": eid,
            "scope": "workspace",
        },
    ).json()
    node = {
        "id": str(uuid4()),
        "label": "Establish conditions",
        "kind": "action",
        "position": {"x": 0, "y": 0},
        "connection_id": connection["id"],
    }
    assert (
        client.put(
            path, json=scenario["content"] | {"nodes": [node]}, headers={"If-Match": '"1"'}
        ).status_code
        == 422
    )
    node["environment_id"] = eid
    assert (
        client.put(
            path, json=scenario["content"] | {"nodes": [node]}, headers={"If-Match": '"1"'}
        ).status_code
        == 200
    )
