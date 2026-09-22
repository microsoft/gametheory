import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from gametheory import preparation_service as service
from gametheory.api import app
from gametheory.auth import Principal, authenticate
from gametheory.persistence import (
    Administrator,
    ApprovalRevocation,
    ApproverGrantRevocation,
    Asset,
    Audit,
    BoardContributor,
    BoardOrigin,
    ConfigurationWithdrawal,
    ConnectionConfigurationRecord,
    ConnectionGrant,
    DispatchIntent,
    Membership,
    PreparationApproval,
    PreparationBoard,
    PreparationPreview,
    WorkspaceApproverGrant,
    now,
)
from gametheory.preparation import (
    BoardCreate,
    BoardDraft,
    ConnectionConfiguration,
    PreparationApprovalInput,
    canonical_digest,
)

pytestmark = pytest.mark.integration


def acting(actor):
    app.dependency_overrides[authenticate] = lambda: actor


def require(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def post_collection(client, path, body):
    return client.post(path, json=body)


def put_approver(client, workspace, oid):
    path = workspace + "/approvers"
    return client.put(path, json={"object_id": oid})


def delete_approver(client, workspace, oid):
    return client.delete(workspace + f"/approvers/{oid}")


def published_board(client, name="Record exercise"):
    wid = require(client.post("/api/workspaces", json={"name": "Preparation test"}), 201)["id"]
    workspace = f"/api/workspaces/{wid}"
    scenario = require(client.post(workspace + "/scenarios", json={"name": name}), 201)
    scenario_path = workspace + f"/scenarios/{scenario['id']}"
    require(client.post(scenario_path + "/revisions", headers={"If-Match": '"1"'}), 201)
    body = {"name": name, "scenario_id": scenario["id"], "revision_version": 1}
    board = require(post_collection(client, workspace + "/boards", body), 201)
    return wid, workspace, scenario, scenario_path, board, workspace + f"/boards/{board['id']}"


def reviewer(client, actor, workspace, role="viewer"):
    other = Principal(actor.tenant, str(uuid4()))
    require(client.put(workspace + "/members", json={"object_id": other.object_id, "role": role}))
    grant = require(put_approver(client, workspace, other.object_id))
    return other, grant


def configuration(client, workspace, *, scope="workspace", classification="nonproduction"):
    environment = require(client.post("/api/environments", json={"name": "Test"}), 201)
    body = {
        "name": "Registered record service",
        "kind": "rest",
        "scope": scope,
        "environment_id": environment["id"],
    }
    if scope == "assigned":
        body["workspace_ids"] = [workspace.rsplit("/", 1)[-1]]
    connection = require(client.post(workspace + "/connections", json=body), 201)
    content = {
        "classification": classification,
        "resource_id": "record-service/example",
        "endpoint": "https://records.example.invalid",
        "identity_ref": "identity/record-reader",
        "catalog": {
            "schema_version": "operation-catalog/v1",
            "name": "Example record registry",
            "operations": [
                {
                    "key": "record.read",
                    "version": "1",
                    "label": "Inspect record",
                    "effect": "read",
                    "invocation": {"kind": "rest", "method": "GET", "path": "/records/{record_id}"},
                    "parameters": [{"name": "record_id", "type": "uuid", "required": True}],
                    "results": [{"name": "quantity", "type": "integer", "required": True}],
                    "recovery": "No target changes.",
                }
            ],
        },
    }
    config = require(
        post_collection(
            client, workspace + f"/connections/{connection['id']}/configurations", content
        ),
        201,
    )
    return connection, content, config


def bound_draft(board, config):
    return board["draft"] | {
        "steps": [
            {
                "id": str(uuid4()),
                "label": "Inspect record",
                "kind": "operation",
                "binding": {
                    "configuration_id": config["id"],
                    "operation_key": "record.read",
                    "operation_version": "1",
                },
                "parameters": {"record_id": str(uuid4())},
            }
        ],
        "recovery": "Read-only review.",
    }


def preview(client, path, version):
    return require(client.post(path + "/previews", headers={"If-Match": f'"{version}"'}), 201)


def decision_body(frozen, **changes):
    return {
        "preview_id": frozen["id"],
        "digest": frozen["digest"],
        "decision": "approved",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "acknowledge_unverified": True,
        "note": "Static preparation; live prerequisites remain unverified.",
    } | changes


def approve(client, path, frozen, **changes):
    return require(
        client.post(
            path + "/approvals",
            json=decision_body(frozen, **changes),
            headers={"If-Match": f'"{frozen["board_version"]}"'},
        ),
        201,
    )


def test_boards_only_start_from_publication_and_pin_exact_versions(sql_client, sql_factory):
    client, actor = sql_client
    wid = require(client.post("/api/workspaces", json={"name": "Pins"}), 201)["id"]
    workspace = f"/api/workspaces/{wid}"
    scenario = require(
        client.post(workspace + "/scenarios", json={"name": "Published content"}), 201
    )
    scenario_path = workspace + f"/scenarios/{scenario['id']}"
    body = {"name": "Pinned board", "scenario_id": scenario["id"], "revision_version": 1}
    assert post_collection(client, workspace + "/boards", body).status_code == 422
    aid = str(uuid4())
    data = b"synthetic application-owned test asset"
    with sql_factory.begin() as db:
        # Metadata fixtures exercise SQL pinning, not live Blob integrity.
        db.add(
            Asset(
                id=aid,
                workspace_id=wid,
                name="brief.txt",
                media_type="text/plain",
                blob_key=f"{wid}/{aid}",
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                state="ready",
                actor=actor.object_id,
            )
        )
    content = scenario["content"] | {"asset_ids": [aid]}
    require(client.put(scenario_path, json=content, headers={"If-Match": '"1"'}))
    require(client.post(scenario_path + "/revisions", headers={"If-Match": '"2"'}), 201)
    board = require(
        post_collection(client, workspace + "/boards", body | {"revision_version": 2}), 201
    )
    path = workspace + f"/boards/{board['id']}"
    frozen = preview(client, path, 1)
    require(
        client.put(
            scenario_path,
            json=content | {"title": "New unpublished draft"},
            headers={"If-Match": '"2"'},
        )
    )
    next_aid = str(uuid4())
    with sql_factory.begin() as db:
        db.add(
            Asset(
                id=next_aid,
                workspace_id=wid,
                previous_id=aid,
                name="brief.txt",
                media_type="text/plain",
                blob_key=f"{wid}/{next_aid}",
                sha256="b" * 64,
                size=20,
                state="ready",
                actor=actor.object_id,
            )
        )
    read = require(client.get(path))
    assert read["scenario"]["content"]["title"] == "Published content"
    assert read["scenario"]["revision_version"] == 2
    assert read["assets"][0]["id"] == aid
    assert read["assets"][0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert preview(client, path, 1)["digest"] == frozen["digest"]
    assert read["draft"]["steps"] == []
    with sql_factory() as db:
        assert db.get(BoardOrigin, board["id"]).scenario == json.dumps(
            read["scenario"], separators=(",", ":"), ensure_ascii=False
        )
        assert db.get(PreparationBoard, board["id"]).version == 1


def test_staged_assets_and_cross_workspace_publication_are_not_usable(sql_client, sql_factory):
    client, actor = sql_client
    wid, workspace, scenario, scenario_path, board, _ = published_board(client)
    other_wid = require(client.post("/api/workspaces", json={"name": "Other"}), 201)["id"]
    other = f"/api/workspaces/{other_wid}"
    assert (
        post_collection(
            client,
            other + "/boards",
            {"name": "Unavailable", "scenario_id": scenario["id"], "revision_version": 1},
        ).status_code
        == 404
    )
    aid = str(uuid4())
    with sql_factory.begin() as db:
        db.add(
            Asset(
                id=aid,
                workspace_id=wid,
                name="incomplete.txt",
                media_type="text/plain",
                blob_key=f"{wid}/{aid}",
                sha256="a" * 64,
                size=10,
                state="staged",
                actor=actor.object_id,
            )
        )
    assert (
        client.put(
            scenario_path,
            json=scenario["content"] | {"asset_ids": [aid]},
            headers={"If-Match": '"1"'},
        ).status_code
        == 422
    )
    assert client.get(other + f"/boards/{board['id']}").status_code == 404
    outsider = Principal(actor.tenant, str(uuid4()))
    acting(outsider)
    assert client.get(workspace + "/boards").status_code == 404
    assert client.get(workspace + f"/boards/{board['id']}").status_code == 404
    acting(Principal(str(uuid4()), actor.object_id))
    assert client.get(workspace + "/boards").status_code == 404


def test_editor_can_prepare_but_cannot_register_targets_or_grant_approval(sql_client):
    client, admin = sql_client
    _, workspace, scenario, _, board, path = published_board(client)
    connection, content, config = configuration(client, workspace)
    editor = Principal(admin.tenant, str(uuid4()))
    require(
        client.put(workspace + "/members", json={"object_id": editor.object_id, "role": "editor"})
    )
    acting(editor)
    assert (
        client.get(workspace + f"/connections/{connection['id']}/configurations").status_code == 200
    )
    assert (
        post_collection(
            client, workspace + f"/connections/{connection['id']}/configurations", content
        ).status_code
        == 403
    )
    assert put_approver(client, workspace, editor.object_id).status_code == 403
    assert (
        client.post(
            workspace + f"/connections/{connection['id']}/configurations/{config['id']}/withdraw",
            headers={"If-Match": '"1"'},
        ).status_code
        == 403
    )
    require(
        post_collection(
            client,
            workspace + "/boards",
            {
                "name": "Editor preparation",
                "scenario_id": scenario["id"],
                "revision_version": 1,
            },
        ),
        201,
    )
    require(client.put(path, json=bound_draft(board, config), headers={"If-Match": '"1"'}))
    frozen = preview(client, path, 2)
    assert (
        client.post(
            path + "/approvals", json=decision_body(frozen), headers={"If-Match": '"2"'}
        ).status_code
        == 403
    )
    acting(admin)
    viewer = Principal(admin.tenant, str(uuid4()))
    require(
        client.put(workspace + "/members", json={"object_id": viewer.object_id, "role": "viewer"})
    )
    acting(viewer)
    assert client.get(path).status_code == 200
    assert client.put(path, json=board["draft"], headers={"If-Match": '"2"'}).status_code == 403
    assert client.post(path + "/previews", headers={"If-Match": '"2"'}).status_code == 403
    assert client.post(path + "/execute", headers={"If-Match": '"2"'}).status_code == 403


def test_configuration_versions_are_immutable_and_workspace_grant_scoped(sql_client, sql_factory):
    client, actor = sql_client
    wid, workspace, _, _, board, path = published_board(client)
    connection, content, first = configuration(client, workspace, scope="organization")
    endpoint = workspace + f"/connections/{connection['id']}/configurations"
    second = require(
        post_collection(client, endpoint, content | {"endpoint": "https://new.example.invalid"}),
        201,
    )
    assert (first["version"], second["version"]) == (1, 2)
    require(client.put(path, json=bound_draft(board, first), headers={"If-Match": '"1"'}))
    frozen = preview(client, path, 2)
    assert frozen["manifest"]["configurations"][0]["id"] == first["id"]
    assert frozen["manifest"]["configurations"][0]["content"]["endpoint"] == content["endpoint"]
    assert first["execution_authorized"] is False
    other_wid, other, _, _, other_board, other_path = published_board(client, "Other registry")
    assert other_wid != wid
    assert require(client.get(other + f"/connections/{connection['id']}/configurations")) == []
    assert (
        client.put(
            other_path, json=bound_draft(other_board, first), headers={"If-Match": '"1"'}
        ).status_code
        == 404
    )
    assert (
        client.post(
            other + f"/connections/{connection['id']}/configurations/{first['id']}/withdraw",
            headers={"If-Match": '"1"'},
        ).status_code
        == 404
    )
    with sql_factory() as db:
        row = db.get(ConnectionConfigurationRecord, first["id"])
        assert json.loads(row.snapshot)["content"]["endpoint"] == content["endpoint"]
    acting(Principal(actor.tenant, str(uuid4())))
    assert client.get(endpoint).status_code == 404


def test_notification_templates_require_exact_ready_workspace_assets(sql_client, sql_factory):
    client, actor = sql_client
    wid, workspace, _, _, _, _ = published_board(client)
    other_wid = require(
        client.post("/api/workspaces", json={"name": "Other template workspace"}), 201
    )["id"]
    environment = require(client.post("/api/environments", json={"name": "Test"}), 201)
    connection = require(
        client.post(
            workspace + "/connections",
            json={
                "name": "Fixed notifications",
                "kind": "graph",
                "scope": "organization",
                "environment_id": environment["id"],
            },
        ),
        201,
    )
    ready_id, staged_id = str(uuid4()), str(uuid4())
    with sql_factory.begin() as db:
        for aid, state in ((ready_id, "ready"), (staged_id, "staged")):
            db.add(
                Asset(
                    id=aid,
                    workspace_id=wid,
                    name="notice.md",
                    media_type="text/markdown",
                    blob_key=f"{wid}/{aid}",
                    sha256="a" * 64,
                    size=10,
                    state=state,
                    actor=actor.object_id,
                )
            )
    content = {
        "classification": "nonproduction",
        "catalog": {
            "name": "Fixed notifications",
            "operations": [
                {
                    "key": "notice.initial",
                    "version": "1",
                    "label": "Fixed notice",
                    "effect": "notify",
                    "invocation": {"kind": "graph", "template_key": "notice-template"},
                    "parameters": [],
                    "results": [],
                    "recovery": "A sent message cannot be undone.",
                }
            ],
        },
        "notification": {
            "template_asset_id": staged_id,
            "sender": "",
            "recipients": [],
            "trusted_link": "",
        },
    }
    endpoint = f"/connections/{connection['id']}/configurations"
    assert post_collection(client, workspace + endpoint, content).status_code == 422
    content["notification"]["template_asset_id"] = ready_id
    registered = require(post_collection(client, workspace + endpoint, content), 201)
    assert registered["template_asset"]["id"] == ready_id
    assert registered["template_asset"]["sha256"] == "a" * 64
    assert (
        post_collection(client, f"/api/workspaces/{other_wid}" + endpoint, content).status_code
        == 422
    )


def test_explicit_grants_never_allow_creator_or_historical_contributor_approval(
    sql_client, sql_factory
):
    client, creator = sql_client
    _, workspace, _, _, board, path = published_board(client)
    frozen = preview(client, path, 1)
    assert (
        client.post(
            path + "/approvals", json=decision_body(frozen), headers={"If-Match": '"1"'}
        ).status_code
        == 403
    )
    require(put_approver(client, workspace, creator.object_id))
    assert (
        client.post(
            path + "/approvals", json=decision_body(frozen), headers={"If-Match": '"1"'}
        ).status_code
        == 403
    )
    contributor, _ = reviewer(client, creator, workspace, role="editor")
    acting(contributor)
    edited = require(
        client.put(
            path,
            json=board["draft"] | {"recovery": "Contributor's review"},
            headers={"If-Match": '"1"'},
        )
    )
    acting(creator)
    require(
        client.put(
            path,
            json=edited["draft"] | {"name": "Later edit by creator"},
            headers={"If-Match": '"2"'},
        )
    )
    frozen = preview(client, path, 3)
    acting(contributor)
    assert (
        client.post(
            path + "/approvals", json=decision_body(frozen), headers={"If-Match": '"3"'}
        ).status_code
        == 403
    )
    second_admin = Principal(creator.tenant, str(uuid4()))
    with sql_factory.begin() as db:
        db.add(Administrator(organization_id=creator.tenant, object_id=second_admin.object_id))
        assert db.get(BoardContributor, (board["id"], contributor.object_id)) is not None
    acting(second_admin)
    assert (
        client.post(
            path + "/approvals", json=decision_body(frozen), headers={"If-Match": '"3"'}
        ).status_code
        == 403
    )
    acting(creator)
    require(put_approver(client, workspace, second_admin.object_id))
    acting(second_admin)
    approval = approve(client, path, frozen)
    assert approval["validity"] == {"valid": True, "reasons": []}
    assert approval["kind"] == "preparation"
    assert approval["execution_authorized"] is False


def test_stale_saves_previews_and_decisions_cannot_reuse_approval(sql_client, sql_factory):
    client, creator = sql_client
    wid, workspace, _, _, board, path = published_board(client)
    other, _ = reviewer(client, creator, workspace)
    frozen = preview(client, path, 1)
    acting(other)
    approval = approve(client, path, frozen, note="Do not copy this review payload into audit")
    acting(creator)
    changed = require(
        client.put(
            path, json=board["draft"] | {"notification_budget": 1}, headers={"If-Match": '"1"'}
        )
    )
    assert changed["version"] == 2
    assert changed["approval_status"] == "invalid"
    assert "board_changed" in changed["current_approval"]["validity"]["reasons"]
    assert client.put(path, json=board["draft"], headers={"If-Match": '"1"'}).status_code == 409
    assert client.post(path + "/previews", headers={"If-Match": '"1"'}).status_code == 409
    newer = preview(client, path, 2)
    assert newer["digest"] != frozen["digest"]
    acting(other)
    assert (
        client.post(
            path + "/approvals", json=decision_body(frozen), headers={"If-Match": '"2"'}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/approvals",
            json=decision_body(newer, digest=frozen["digest"]),
            headers={"If-Match": '"2"'},
        ).status_code
        == 409
    )
    with sql_factory() as db:
        stored = db.get(PreparationApproval, approval["id"])
        assert stored.digest == frozen["digest"]
        assert stored.board_version == 1
        assert db.get(PreparationPreview, frozen["id"]).digest == frozen["digest"]
        audits = list(db.scalars(select(Audit).where(Audit.workspace_id == wid)))
        assert all("review payload" not in str(vars(event)) for event in audits)
        assert (
            db.scalar(
                select(func.count())
                .select_from(PreparationApproval)
                .where(PreparationApproval.board_id == board["id"])
            )
            == 1
        )


def test_explicit_expiry_rejection_revocation_and_grant_generation(
    sql_client, sql_factory, monkeypatch
):
    client, creator = sql_client
    _, workspace, _, _, _, path = published_board(client)
    other, first_grant = reviewer(client, creator, workspace)
    frozen = preview(client, path, 1)
    acting(other)
    for expiry in (
        (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        (datetime.now(UTC) + timedelta(days=31)).isoformat(),
        "2026-12-01T00:00:00",
    ):
        assert (
            client.post(
                path + "/approvals",
                json=decision_body(frozen, expires_at=expiry),
                headers={"If-Match": '"1"'},
            ).status_code
            == 422
        )
    approval = approve(client, path, frozen)
    with monkeypatch.context() as clock:
        clock.setattr(service, "now", lambda: now() + timedelta(hours=2))
        read = require(client.get(path + "/approvals"))[0]
        assert read["validity"]["valid"] is False
        assert "expired" in read["validity"]["reasons"]
    rejected = approve(client, path, frozen, decision="rejected")
    assert rejected["validity"]["valid"] is False
    assert "rejected" in rejected["validity"]["reasons"]
    assert "superseded" in require(client.get(path + "/approvals"))[1]["validity"]["reasons"]
    renewed = approve(client, path, frozen)
    require(client.post(path + f"/approvals/{renewed['id']}/revoke", headers={"If-Match": '"1"'}))
    assert "revoked" in require(client.get(path + "/approvals"))[0]["validity"]["reasons"]
    current = approve(client, path, frozen)
    acting(creator)
    assert delete_approver(client, workspace, other.object_id).status_code == 204
    assert (
        "approver_grant_revoked"
        in require(client.get(path))["current_approval"]["validity"]["reasons"]
    )
    second_grant = require(put_approver(client, workspace, other.object_id))
    assert second_grant["id"] != first_grant["id"]
    assert require(client.get(path))["current_approval"]["validity"]["valid"] is False
    with sql_factory() as db:
        assert db.get(PreparationApproval, current["id"]).grant_id == first_grant["id"]
        assert db.get(PreparationApproval, approval["id"]).kind == "preparation"
        assert db.get(ApproverGrantRevocation, first_grant["id"]) is not None


def test_workspace_access_removal_permanently_invalidates_prior_decisions(sql_client, sql_factory):
    client, creator = sql_client
    _, workspace, _, _, _, path = published_board(client)
    other, grant = reviewer(client, creator, workspace)
    frozen = preview(client, path, 1)
    acting(other)
    approved = approve(client, path, frozen)
    acting(creator)
    assert client.delete(workspace + f"/members/{other.object_id}").status_code == 204
    current = require(client.get(path))["current_approval"]
    assert {"revoked", "workspace_access_removed"} <= set(current["validity"]["reasons"])
    require(
        client.put(workspace + "/members", json={"object_id": other.object_id, "role": "viewer"})
    )
    assert "revoked" in require(client.get(path))["current_approval"]["validity"]["reasons"]
    with sql_factory() as db:
        assert db.get(ApprovalRevocation, approved["id"]).reason == "workspace_access_revoked"
        assert db.get(WorkspaceApproverGrant, grant["id"]) is not None


def test_withdrawal_blocks_new_and_current_approval_without_rewriting_snapshots(
    sql_client, sql_factory
):
    client, creator = sql_client
    _, workspace, _, _, board, path = published_board(client)
    connection, _, config = configuration(client, workspace)
    require(client.put(path, json=bound_draft(board, config), headers={"If-Match": '"1"'}))
    other, _ = reviewer(client, creator, workspace)
    frozen = preview(client, path, 2)
    acting(other)
    approved = approve(client, path, frozen)
    acting(creator)
    endpoint = workspace + f"/connections/{connection['id']}/configurations/{config['id']}/withdraw"
    assert client.post(endpoint).status_code == 428
    assert client.post(endpoint, headers={"If-Match": '"2"'}).status_code == 409
    withdrawn = require(client.post(endpoint, headers={"If-Match": '"1"'}))
    assert withdrawn["withdrawn_at"]
    assert (
        require(client.post(endpoint, headers={"If-Match": '"1"'}))["withdrawn_at"]
        == withdrawn["withdrawn_at"]
    )
    assert (
        "configuration_withdrawn"
        in require(client.get(path))["current_approval"]["validity"]["reasons"]
    )
    assert client.post(path + "/previews", headers={"If-Match": '"2"'}).status_code == 409
    acting(other)
    assert require(client.get(path))["can_approve"] is False
    assert (
        client.post(
            path + "/approvals", json=decision_body(frozen), headers={"If-Match": '"2"'}
        ).status_code
        == 409
    )
    with sql_factory() as db:
        assert db.get(PreparationApproval, approved["id"]).digest == frozen["digest"]
        assert (
            json.loads(db.get(ConnectionConfigurationRecord, config["id"]).snapshot)["content"]
            == config["content"]
        )
        assert db.get(ConfigurationWithdrawal, config["id"]) is not None


def test_connection_grant_revocation_denies_preview_configuration_disclosure(
    sql_client, sql_factory
):
    client, creator = sql_client
    wid, workspace, _, _, board, path = published_board(client)
    connection, _, config = configuration(client, workspace, scope="assigned")
    require(client.put(path, json=bound_draft(board, config), headers={"If-Match": '"1"'}))
    other, _ = reviewer(client, creator, workspace)
    frozen = preview(client, path, 2)
    acting(other)
    approve(client, path, frozen)
    with sql_factory.begin() as db:
        db.execute(
            delete(ConnectionGrant).where(
                ConnectionGrant.connection_id == connection["id"],
                ConnectionGrant.workspace_id == wid,
            )
        )
    assert (
        client.get(workspace + f"/connections/{connection['id']}/configurations").status_code == 404
    )
    assert client.get(path + "/previews").status_code == 404
    read = require(client.get(path))
    assert read["latest_preview"] is None
    assert "configuration_unavailable" in read["current_approval"]["validity"]["reasons"]
    assert read["can_approve"] is False
    assert "https://records.example.invalid" not in json.dumps(read)


@pytest.mark.parametrize("classification", ["unknown", "production"])
def test_unclassified_and_production_never_become_execution_eligible(
    sql_client, sql_factory, classification
):
    client, creator = sql_client
    wid, workspace, _, _, board, path = published_board(client)
    _, _, config = configuration(client, workspace, classification=classification)
    require(client.put(path, json=bound_draft(board, config), headers={"If-Match": '"1"'}))
    other, _ = reviewer(client, creator, workspace)
    frozen = preview(client, path, 2)
    assert frozen["execution_eligible"] is False
    assert "target_classification_ineligible" in {finding["code"] for finding in frozen["findings"]}
    acting(other)
    result = approve(client, path, frozen)
    assert result["kind"] == "preparation"
    assert result["execution_authorized"] is False
    acting(creator)
    with sql_factory() as db:
        before = db.scalar(select(func.count()).select_from(DispatchIntent))
    response = client.post(
        path + "/execute", json={"execution_authorized": True}, headers={"If-Match": '"2"'}
    )
    assert response.status_code == 501
    assert response.json()["execution_authorized"] is False
    with sql_factory() as db:
        assert db.scalar(select(func.count()).select_from(DispatchIntent)) == before
        assert (
            db.scalar(
                select(Audit.id).where(
                    Audit.workspace_id == wid, Audit.operation == "execution.disabled"
                )
            )
            is not None
        )


def test_invalid_bound_flow_is_rejected_without_save_or_audit(sql_client, sql_factory):
    client, _ = sql_client
    wid, workspace, _, _, board, path = published_board(client)
    _, _, config = configuration(client, workspace)
    good = bound_draft(board, config)
    bad_type = json.loads(json.dumps(good))
    bad_type["steps"][0]["parameters"]["record_id"] = 42
    assert client.put(path, json=bad_type, headers={"If-Match": '"1"'}).status_code == 422
    wrong_operation = json.loads(json.dumps(good))
    wrong_operation["steps"][0]["binding"]["operation_version"] = "other-version"
    assert client.put(path, json=wrong_operation, headers={"If-Match": '"1"'}).status_code == 422
    cyclic = json.loads(json.dumps(good))
    cyclic["steps"][0]["depends_on"] = [cyclic["steps"][0]["id"]]
    assert client.put(path, json=cyclic, headers={"If-Match": '"1"'}).status_code == 422
    with sql_factory() as db:
        assert db.get(PreparationBoard, board["id"]).version == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.workspace_id == wid, Audit.operation == "board.saved")
            )
            == 0
        )


def test_prior_result_bindings_persist_in_previews_and_require_fresh_approval(
    sql_client, sql_factory
):
    client, creator = sql_client
    _, workspace, _, _, board, path = published_board(client)
    connection, content, _ = configuration(client, workspace)
    content["catalog"]["operations"].append(
        {
            "key": "record.create",
            "version": "1",
            "label": "Create owned record",
            "effect": "write",
            "invocation": {"kind": "rest", "method": "POST", "path": "/records"},
            "parameters": [],
            "results": [
                {"name": "record_id", "type": "uuid", "required": True},
                {"name": "alternate_id", "type": "uuid", "required": True},
            ],
            "recovery": "Authorized owner reviews ownership and record versions before recovery.",
        }
    )
    config = require(
        post_collection(
            client, workspace + f"/connections/{connection['id']}/configurations", content
        ),
        201,
    )
    draft = bound_draft(board, config)
    source = {
        "id": str(uuid4()),
        "kind": "operation",
        "label": "Create record",
        "binding": {
            "configuration_id": config["id"],
            "operation_key": "record.create",
            "operation_version": "1",
        },
    }
    observe = draft["steps"][0]
    reference = {"source_step_id": source["id"], "field": "record_id"}
    observe["depends_on"] = [source["id"]]
    observe["parameters"] = {"record_id": reference}
    draft["steps"].insert(0, source)
    draft["recovery"] = "Authorized owner reviews ownership and record versions before recovery."
    saved = require(client.put(path, json=draft, headers={"If-Match": '"1"'}))
    assert saved["draft"]["steps"][1]["parameters"]["record_id"] == reference
    frozen = preview(client, path, 2)
    assert frozen["manifest"]["draft"]["steps"][1]["parameters"]["record_id"] == reference
    assert "result_binding_unverified" in {finding["code"] for finding in frozen["findings"]}
    other, _ = reviewer(client, creator, workspace)
    acting(other)
    approved = approve(client, path, frozen)
    assert approved["validity"]["valid"] is True
    assert approved["execution_authorized"] is False
    acting(creator)
    changed = saved["draft"]
    changed["steps"][1]["parameters"]["record_id"]["field"] = "alternate_id"
    updated = require(client.put(path, json=changed, headers={"If-Match": '"2"'}))
    assert "board_changed" in updated["current_approval"]["validity"]["reasons"]
    newer = preview(client, path, 3)
    assert newer["digest"] != frozen["digest"]
    with sql_factory() as db:
        original = json.loads(db.get(PreparationPreview, frozen["id"]).manifest)
        assert original["draft"]["steps"][1]["parameters"]["record_id"]["field"] == "record_id"
        assert db.get(PreparationApproval, approved["id"]).digest == frozen["digest"]
        assert (
            json.loads(db.get(PreparationBoard, board["id"]).draft)["steps"][1]["parameters"][
                "record_id"
            ]["field"]
            == "alternate_id"
        )


def test_edit_versus_approval_race_never_leaves_valid_stale_approval(sql_client, sql_factory):
    client, creator = sql_client
    wid, workspace, _, _, board, path = published_board(client)
    other, _ = reviewer(client, creator, workspace)
    frozen = preview(client, path, 1)
    barrier = Barrier(2)

    def edit():
        barrier.wait(timeout=10)
        with sql_factory.begin() as db:
            db.execute(text("SET LOCK_TIMEOUT 10000"))
            service.save_board(
                db,
                creator,
                wid,
                board["id"],
                BoardDraft.model_validate(board["draft"] | {"name": "Concurrent edit"}),
                1,
                str(uuid4()),
            )
            return 200

    def review():
        barrier.wait(timeout=10)
        try:
            with sql_factory.begin() as db:
                db.execute(text("SET LOCK_TIMEOUT 10000"))
                service.decide_preparation(
                    db,
                    other,
                    wid,
                    board["id"],
                    PreparationApprovalInput.model_validate(decision_body(frozen)),
                    1,
                    str(uuid4()),
                )
                return 201
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        edit_future, approval_future = executor.submit(edit), executor.submit(review)
        assert edit_future.result(timeout=30) == 200
        assert approval_future.result(timeout=30) in {201, 409}
    read = require(client.get(path))
    assert read["version"] == 2
    assert read["approval_status"] != "approved"
    assert all(
        not approval["validity"]["valid"] for approval in require(client.get(path + "/approvals"))
    )


def test_preview_versus_edit_race_pins_only_the_matched_saved_version(sql_client, sql_factory):
    client, creator = sql_client
    wid, _, _, _, board, path = published_board(client)
    barrier = Barrier(2)

    def freeze():
        barrier.wait(timeout=10)
        try:
            with sql_factory.begin() as db:
                db.execute(text("SET LOCK_TIMEOUT 10000"))
                result = service.freeze_preview(db, creator, wid, board["id"], 1, str(uuid4()))
                assert result.manifest.draft.name == board["name"]
                return 201
        except HTTPException as exc:
            return exc.status_code

    def edit():
        barrier.wait(timeout=10)
        with sql_factory.begin() as db:
            db.execute(text("SET LOCK_TIMEOUT 10000"))
            service.save_board(
                db,
                creator,
                wid,
                board["id"],
                BoardDraft.model_validate(board["draft"] | {"name": "After concurrent save"}),
                1,
                str(uuid4()),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        freeze_future, edit_future = executor.submit(freeze), executor.submit(edit)
        edit_future.result(timeout=30)
        assert freeze_future.result(timeout=30) in {201, 409}
    assert require(client.get(path))["version"] == 2
    for frozen in require(client.get(path + "/previews")):
        assert frozen["board_version"] == 1
        assert frozen["is_current"] is False
        assert frozen["digest"] == canonical_digest(frozen["manifest"])


@pytest.mark.parametrize("revocation", ["membership", "approver", "configuration", "connection"])
def test_authorization_and_withdrawal_rows_are_locked_through_approval_commit(
    sql_client, sql_factory, revocation
):
    client, creator = sql_client
    wid, workspace, _, _, board, path = published_board(client)
    connection, _, config = configuration(client, workspace, scope="assigned")
    require(client.put(path, json=bound_draft(board, config), headers={"If-Match": '"1"'}))
    other, grant = reviewer(client, creator, workspace)
    frozen = preview(client, path, 2)

    def competing_revocation():
        try:
            with sql_factory.begin() as db:
                db.execute(text("SET LOCK_TIMEOUT 400"))
                if revocation == "membership":
                    db.execute(
                        delete(Membership).where(
                            Membership.workspace_id == wid, Membership.object_id == other.object_id
                        )
                    )
                elif revocation == "approver":
                    db.add(
                        ApproverGrantRevocation(
                            grant_id=grant["id"],
                            actor=creator.object_id,
                            correlation_id=str(uuid4()),
                        )
                    )
                    db.flush()
                elif revocation == "configuration":
                    db.add(
                        ConfigurationWithdrawal(
                            configuration_id=config["id"],
                            actor=creator.object_id,
                            correlation_id=str(uuid4()),
                        )
                    )
                    db.flush()
                else:
                    db.execute(
                        delete(ConnectionGrant).where(
                            ConnectionGrant.connection_id == connection["id"],
                            ConnectionGrant.workspace_id == wid,
                        )
                    )
            return "committed"
        except DBAPIError as exc:
            if "1222" not in str(exc.orig):
                raise
            return "locked"

    with sql_factory() as db:
        service.decide_preparation(
            db,
            other,
            wid,
            board["id"],
            PreparationApprovalInput.model_validate(decision_body(frozen)),
            2,
            str(uuid4()),
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(competing_revocation).result(timeout=15) == "locked"
        db.commit()
    assert competing_revocation() == "committed"
    reasons = require(client.get(path + "/approvals"))[0]["validity"]["reasons"]
    expected = {
        "membership": "workspace_access_removed",
        "approver": "approver_grant_revoked",
        "configuration": "configuration_withdrawn",
        "connection": "configuration_unavailable",
    }
    assert expected[revocation] in reasons


def test_concurrent_stale_saves_have_one_winner(sql_client, sql_factory):
    client, creator = sql_client
    wid, _, _, _, board, path = published_board(client)
    barrier = Barrier(2)

    def save(name):
        barrier.wait(timeout=10)
        try:
            with sql_factory.begin() as db:
                db.execute(text("SET LOCK_TIMEOUT 10000"))
                service.save_board(
                    db, creator, wid, board["id"], BoardDraft(name=name), 1, str(uuid4())
                )
                return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save, name) for name in ("First writer", "Second writer")]
        assert sorted(future.result(timeout=30) for future in futures) == [200, 409]
    assert require(client.get(path))["version"] == 2


def test_append_and_grant_protocol_preserves_history_without_collection_tokens(
    sql_client, sql_factory
):
    client, actor = sql_client
    wid, workspace, scenario, scenario_path, _, _ = published_board(client)
    boards_path = workspace + "/boards"
    initial_boards = client.get(boards_path)
    assert "etag" not in initial_boards.headers
    create_body = {"name": "Another board", "scenario_id": scenario["id"], "revision_version": 1}
    assert client.post(boards_path, json=create_body | {"revision_version": 99}).status_code == 422
    assert len(require(client.get(boards_path))) == 1
    require(
        client.put(
            scenario_path,
            json=scenario["content"] | {"title": "New mutable source"},
            headers={"If-Match": '"1"'},
        )
    )
    created = client.post(boards_path, json=create_body)
    assert created.status_code == 201, created.text
    assert created.headers["etag"] == '"1"'
    assert "x-collection-etag" not in created.headers
    assert created.json()["scenario"]["content"]["title"] == scenario["content"]["title"]
    connection, content, config = configuration(client, workspace)
    configurations = workspace + f"/connections/{connection['id']}/configurations"
    assert "etag" not in client.get(configurations).headers
    assert client.post(configurations + f"/{config['id']}/withdraw").status_code == 428
    assert (
        client.post(
            configurations + f"/{config['id']}/withdraw", headers={"If-Match": '"2"'}
        ).status_code
        == 409
    )
    withdrawn = client.post(
        configurations + f"/{config['id']}/withdraw", headers={"If-Match": '"1"'}
    )
    assert withdrawn.status_code == 200
    assert withdrawn.headers["etag"] == '"1"'
    assert "x-collection-etag" not in withdrawn.headers
    registered = client.post(configurations, json=content)
    assert registered.status_code == 201
    assert registered.json()["version"] == 2
    assert registered.headers["etag"] == '"2"'
    assert "x-collection-etag" not in registered.headers
    history = require(client.get(configurations))
    assert [entry["version"] for entry in history] == [2, 1]
    assert history[1]["content"] == config["content"]
    assert history[1]["withdrawn_at"] is not None
    approvers = workspace + "/approvers"
    other = str(uuid4())
    require(client.put(workspace + "/members", json={"object_id": other, "role": "viewer"}))
    assert "etag" not in client.get(approvers).headers
    granted = client.put(approvers, json={"object_id": other})
    assert granted.status_code == 200
    assert "etag" not in granted.headers
    unchanged = client.put(approvers, json={"object_id": other})
    assert unchanged.status_code == 200
    assert unchanged.json()["id"] == granted.json()["id"]
    revoked = client.delete(approvers + f"/{other}")
    assert revoked.status_code == 204
    assert "etag" not in revoked.headers
    assert require(client.get(approvers)) == []
    restored = client.put(approvers, json={"object_id": other})
    assert restored.status_code == 200
    assert restored.json()["id"] != granted.json()["id"]
    with sql_factory() as db:
        assert db.get(ApproverGrantRevocation, granted.json()["id"]) is not None
        assert service.active_grant(db, wid, other).granted_by == actor.object_id


def test_concurrent_board_creation_appends_distinct_pinned_boards(sql_client, sql_factory):
    client, actor = sql_client
    wid, workspace, scenario, _, _, _ = published_board(client)
    barrier = Barrier(2)

    def create(name):
        barrier.wait(timeout=10)
        with sql_factory.begin() as db:
            db.execute(text("SET LOCK_TIMEOUT 10000"))
            return service.create_board(
                db,
                actor,
                wid,
                BoardCreate(name=name, scenario_id=scenario["id"], revision_version=1),
                str(uuid4()),
            ).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(create, name) for name in ("First board", "Second board")]
        assert len({future.result(timeout=30) for future in futures}) == 2
    read = client.get(workspace + "/boards")
    assert "etag" not in read.headers
    assert len(read.json()) == 3
    assert all(entry["revision_version"] == 1 for entry in read.json())


def test_concurrent_configuration_appends_serialize_unique_revisions(sql_client, sql_factory):
    client, actor = sql_client
    wid, workspace, _, _, _, _ = published_board(client)
    connection, content, _ = configuration(client, workspace)
    cid = connection["id"]
    barrier = Barrier(2)

    def register():
        barrier.wait(timeout=10)
        with sql_factory.begin() as db:
            db.execute(text("SET LOCK_TIMEOUT 10000"))
            return service.register_configuration(
                db, actor, wid, cid, ConnectionConfiguration.model_validate(content), str(uuid4())
            ).version

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(register) for _ in range(2)]
        assert sorted(future.result(timeout=30) for future in futures) == [2, 3]
    read = client.get(workspace + f"/connections/{cid}/configurations")
    assert "etag" not in read.headers
    assert [row["version"] for row in read.json()] == [3, 2, 1]


def test_concurrent_grants_are_idempotent_and_regrant_gets_a_new_identity(sql_client, sql_factory):
    client, actor = sql_client
    wid, workspace, _, _, _, _ = published_board(client)
    other = Principal(actor.tenant, str(uuid4()))
    require(
        client.put(workspace + "/members", json={"object_id": other.object_id, "role": "viewer"})
    )
    barrier = Barrier(2)

    def grant():
        barrier.wait(timeout=10)
        with sql_factory.begin() as db:
            db.execute(text("SET LOCK_TIMEOUT 10000"))
            return service.grant_approver(db, actor, wid, other.object_id, str(uuid4())).id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(grant) for _ in range(2)]
        ids = {future.result(timeout=30) for future in futures}
        assert len(ids) == 1
    read = client.get(workspace + "/approvers")
    assert "etag" not in read.headers
    assert len(require(read)) == 1
    assert delete_approver(client, workspace, other.object_id).status_code == 204
    restored = require(put_approver(client, workspace, other.object_id))
    assert restored["id"] not in {str(grant_id) for grant_id in ids}
