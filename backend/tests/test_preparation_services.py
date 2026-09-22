from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import mssql

from gametheory import preparation_service as service
from gametheory.auth import Principal
from gametheory.domain import ScenarioContent
from gametheory.persistence import (
    BoardContributor,
    PreparationApproval,
    PreparationBoard,
    PreparationPreview,
    now,
)
from gametheory.preparation import (
    BoardCreate,
    BoardDraft,
    ConnectionConfiguration,
    PreparationManifest,
    ScenarioPin,
    canonical_digest,
)


def test_workspace_authorization_fence_uses_transaction_held_sql_server_locks(monkeypatch):
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(id=str(uuid4()))
    actor = Principal(str(uuid4()), str(uuid4()))
    authorize = MagicMock(return_value="editor")
    monkeypatch.setattr(service, "authorize", authorize)
    assert (
        service.preparation_workspace(db, actor, db.scalar.return_value.id, "editor", mutation=True)
        == "editor"
    )
    sql = str(db.scalar.call_args.args[0].compile(dialect=mssql.dialect()))
    assert "WITH (UPDLOCK, HOLDLOCK)" in sql
    assert "workspaces.organization_id =" in sql
    assert authorize.call_args.kwargs["fence"] is True


def test_approver_capability_read_fences_revocation_key_range():
    db = MagicMock()
    service.active_grant(db, str(uuid4()), str(uuid4()))
    sql = str(db.scalar.call_args.args[0].compile(dialect=mssql.dialect()))
    assert "workspace_approver_grants WITH (HOLDLOCK)" in sql
    assert "approver_grant_revocations WITH (HOLDLOCK)" in sql
    assert "NOT (EXISTS" in sql


def test_locked_board_read_rejects_stale_etag_before_any_write(monkeypatch):
    db = MagicMock()
    actor = Principal(str(uuid4()), str(uuid4()))
    board = SimpleNamespace(id=str(uuid4()), workspace_id=str(uuid4()), version=2)
    db.scalar.return_value = board
    monkeypatch.setattr(service, "preparation_workspace", lambda *_args, **_kwargs: "editor")
    with pytest.raises(HTTPException) as error:
        service.board_record(
            db, actor, board.workspace_id, board.id, "editor", mutation=True, version=1
        )
    assert error.value.status_code == 409
    assert "WITH (UPDLOCK, HOLDLOCK)" in str(
        db.scalar.call_args.args[0].compile(dialect=mssql.dialect())
    )
    db.execute.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "method,args",
    [
        ("create_board", (BoardCreate(name="Board", scenario_id=uuid4(), revision_version=1),)),
        (
            "register_configuration",
            (
                str(uuid4()),
                ConnectionConfiguration(catalog={"name": "Operations", "operations": []}),
            ),
        ),
        ("grant_approver", (str(uuid4()),)),
        ("revoke_approver", (str(uuid4()),)),
    ],
)
def test_unconditional_append_and_grant_operations_still_fence_authorization(
    monkeypatch, method, args
):
    db = MagicMock()
    fence = MagicMock(side_effect=HTTPException(403, "Workspace access denied"))
    monkeypatch.setattr(service, "preparation_workspace", fence)
    with pytest.raises(HTTPException) as error:
        getattr(service, method)(
            db, Principal(str(uuid4()), str(uuid4())), str(uuid4()), *args, str(uuid4())
        )
    assert error.value.status_code == 403
    fence.assert_called_once()
    assert fence.call_args.kwargs["mutation"] is True
    db.scalar.assert_not_called()
    db.execute.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize(
    "change,reason",
    [
        (None, None),
        ("rejected", "rejected"),
        ("superseded", "superseded"),
        ("revoked", "revoked"),
        ("expired", "expired"),
        ("board_changed", "board_changed"),
        ("draft_changed_without_version", "board_changed"),
        ("no_grant", "approver_grant_revoked"),
        ("grant_generation", "approver_grant_revoked"),
        ("access_removed", "workspace_access_removed"),
        ("creator", "self_approval"),
        ("contributor", "self_approval"),
        ("preview_changed", "preview_mismatch"),
        ("digest_changed", "preview_mismatch"),
    ],
)
def test_current_validity_is_computed_without_rewriting_history(monkeypatch, change, reason):
    db = MagicMock()
    actor = Principal(str(uuid4()), str(uuid4()))
    board = PreparationBoard(
        id=str(uuid4()),
        workspace_id=str(uuid4()),
        version=1,
        name="Review",
        draft=BoardDraft(name="Review").model_dump_json(),
    )
    reviewer = str(uuid4())
    origin = SimpleNamespace(created_by=actor.object_id)
    manifest = PreparationManifest(
        board_id=UUID(board.id),
        workspace_id=UUID(board.workspace_id),
        board_version=1,
        draft=BoardDraft(name="Review"),
        scenario=ScenarioPin(
            scenario_id=uuid4(), revision_version=1, content=ScenarioContent(title="Published")
        ),
        assets=[],
        configurations=[],
    )
    frozen = PreparationPreview(
        id=str(uuid4()),
        board_id=board.id,
        board_version=1,
        digest=canonical_digest(manifest.model_dump(mode="json")),
        manifest=manifest.model_dump_json(),
    )
    approval = PreparationApproval(
        id=str(uuid4()),
        board_id=board.id,
        board_version=1,
        preview_id=frozen.id,
        reviewer=reviewer,
        digest=frozen.digest,
        decision="approved",
        expires_at=now() + timedelta(hours=1),
        grant_id=str(uuid4()),
    )
    grant = SimpleNamespace(id=approval.grant_id)
    revoke = None
    current = approval
    contributor = None
    authorize = MagicMock(return_value="viewer")
    if change == "rejected":
        approval.decision = "rejected"
    elif change == "superseded":
        current = SimpleNamespace(id=str(uuid4()))
    elif change == "revoked":
        revoke = SimpleNamespace(actor=actor.object_id)
    elif change == "expired":
        cutoff = now()
        approval.expires_at = cutoff
        monkeypatch.setattr(service, "now", lambda: cutoff)
    elif change == "board_changed":
        board.version = 2
    elif change == "draft_changed_without_version":
        board.draft = BoardDraft(name="Review", recovery="Changed after preview").model_dump_json()
    elif change == "no_grant":
        grant = None
    elif change == "grant_generation":
        grant.id = str(uuid4())
    elif change == "access_removed":
        authorize.side_effect = HTTPException(404)
    elif change == "creator":
        origin.created_by = reviewer
    elif change == "contributor":
        contributor = SimpleNamespace(object_id=reviewer)
    elif change == "preview_changed":
        frozen.board_version = 2
    elif change == "digest_changed":
        frozen.manifest = frozen.manifest.replace('"Review"', '"Changed"')
    db.get.side_effect = lambda model, _key: (
        contributor
        if model is BoardContributor
        else frozen
        if model is PreparationPreview
        else None
    )
    monkeypatch.setattr(service, "origin_record", lambda *_: origin)
    monkeypatch.setattr(service, "latest_approval", lambda *_: current)
    monkeypatch.setattr(service, "active_grant", lambda *_: grant)
    monkeypatch.setattr(service, "approval_revocation", lambda *_: revoke)
    monkeypatch.setattr(service, "authorize", authorize)
    validity = service.approval_validity(db, actor, board, approval)
    assert validity.valid is (reason is None)
    if reason:
        assert reason in validity.reasons
    assert authorize.call_args.kwargs["fence"] is True
    assert approval.digest == canonical_digest(manifest.model_dump(mode="json"))
    db.add.assert_not_called()
    db.execute.assert_not_called()
