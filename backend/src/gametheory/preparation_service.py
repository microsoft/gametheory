"""Transactional preparation services. No target clients or execution dispatch."""

import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from gametheory.auth import Principal, authorize, is_admin, require_admin
from gametheory.domain import Role, ScenarioContent
from gametheory.persistence import (
    ApprovalRevocation,
    ApproverGrantRevocation,
    Asset,
    BoardContributor,
    BoardOrigin,
    ConfigurationWithdrawal,
    Connection,
    ConnectionConfigurationRecord,
    Environment,
    PreparationApproval,
    PreparationBoard,
    PreparationPreview,
    Revision,
    Scenario,
    Workspace,
    WorkspaceApproverGrant,
    new_id,
    now,
)
from gametheory.preparation import (
    ApprovalValidity,
    ApproverGrantView,
    AssetPin,
    BoardCreate,
    BoardDraft,
    BoardSummary,
    BoardView,
    ConfigurationSnapshot,
    ConfigurationView,
    ConnectionConfiguration,
    PreparationApprovalInput,
    PreparationApprovalView,
    PreparationFinding,
    PreparationManifest,
    PreparationPreviewView,
    ScenarioPin,
    canonical_digest,
    canonical_json,
    preparation_findings,
    validate_bindings,
    validate_configuration_kind,
)
from gametheory.service import audit, connection_available

_ASSETS = TypeAdapter(list[AssetPin])
_FINDINGS = TypeAdapter(list[PreparationFinding])


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC)


def preparation_workspace(
    db: Session, actor: Principal, wid: str, role: str = "viewer", *, mutation: bool = False
) -> Role:
    # HOLDLOCK survives READ_COMMITTED_SNAPSHOT. The workspace update lock orders
    # all preparation mutations without UPDATE permission on immutable tables.
    workspace = db.scalar(
        select(Workspace)
        .where(Workspace.id == wid, Workspace.organization_id == actor.tenant)
        .with_hint(
            Workspace, "WITH (UPDLOCK, HOLDLOCK)" if mutation else "WITH (HOLDLOCK)", "mssql"
        )
        .execution_options(populate_existing=True)
    )
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    return authorize(db, actor, wid, role, fence=True)


def available_connection(db: Session, actor: Principal, wid: str, cid: str) -> Connection:
    connection = db.scalar(
        select(Connection)
        .where(Connection.id == cid, Connection.organization_id == actor.tenant)
        .with_hint(Connection, "WITH (HOLDLOCK)", "mssql")
        .execution_options(populate_existing=True)
    )
    if connection is None or not connection_available(db, connection, wid, fence=True):
        raise HTTPException(404, "Connection is unavailable in this workspace")
    environment = db.scalar(
        select(Environment)
        .where(
            Environment.id == connection.environment_id, Environment.organization_id == actor.tenant
        )
        .with_hint(Environment, "WITH (HOLDLOCK)", "mssql")
        .execution_options(populate_existing=True)
    )
    if environment is None:
        raise HTTPException(404, "Connection is unavailable in this workspace")
    return connection


def pin_asset(db: Session, wid: str, aid: str) -> AssetPin:
    asset = db.scalar(
        select(Asset)
        .where(Asset.id == aid, Asset.workspace_id == wid, Asset.state == "ready")
        .with_hint(Asset, "WITH (HOLDLOCK)", "mssql")
        .execution_options(populate_existing=True)
    )
    if asset is None:
        raise HTTPException(422, "An exact ready asset version is unavailable in this workspace")
    return AssetPin(
        id=UUID(asset.id),
        name=asset.name,
        media_type=asset.media_type,
        sha256=asset.sha256,
        size=asset.size,
    )


def withdrawal(db: Session, config_id: str) -> ConfigurationWithdrawal | None:
    return db.scalar(
        select(ConfigurationWithdrawal)
        .where(ConfigurationWithdrawal.configuration_id == config_id)
        .with_hint(ConfigurationWithdrawal, "WITH (HOLDLOCK)", "mssql")
    )


def configuration_record(
    db: Session, actor: Principal, wid: str, config_id: str, *, active: bool = False
) -> ConnectionConfigurationRecord:
    record = db.scalar(
        select(ConnectionConfigurationRecord)
        .where(
            ConnectionConfigurationRecord.id == config_id,
            ConnectionConfigurationRecord.workspace_id == wid,
        )
        .with_hint(ConnectionConfigurationRecord, "WITH (HOLDLOCK)", "mssql")
    )
    if record is None:
        raise HTTPException(404, "Configuration is unavailable in this workspace")
    connection = available_connection(db, actor, wid, record.connection_id)
    snapshot = ConfigurationSnapshot.model_validate_json(record.snapshot)
    environment = db.get(Environment, connection.environment_id)
    if (
        snapshot.connection_id != UUID(connection.id)
        or snapshot.workspace_id != UUID(wid)
        or snapshot.connection_kind != connection.kind
        or snapshot.environment_id != UUID(connection.environment_id)
        or environment is None
        or snapshot.environment_name != environment.name
    ):
        raise HTTPException(
            409, "Pinned connection inventory changed; register a new configuration"
        )
    if active and withdrawal(db, record.id) is not None:
        raise HTTPException(409, "Configuration was withdrawn; bind a new registered version")
    return record


def configuration_view(db: Session, record: ConnectionConfigurationRecord) -> ConfigurationView:
    snapshot = ConfigurationSnapshot.model_validate_json(record.snapshot)
    withdrawn = withdrawal(db, record.id)
    return ConfigurationView(
        **snapshot.model_dump(),
        withdrawn_at=utc(withdrawn.created_at) if withdrawn else None,
        withdrawn_by=UUID(withdrawn.actor) if withdrawn else None,
    )


def list_configurations(
    db: Session, actor: Principal, wid: str, cid: str
) -> list[ConfigurationView]:
    preparation_workspace(db, actor, wid)
    available_connection(db, actor, wid, cid)
    return [
        configuration_view(db, record)
        for record in db.scalars(
            select(ConnectionConfigurationRecord)
            .where(
                ConnectionConfigurationRecord.workspace_id == wid,
                ConnectionConfigurationRecord.connection_id == cid,
            )
            .order_by(ConnectionConfigurationRecord.version.desc())
        )
    ]


def register_configuration(
    db: Session,
    actor: Principal,
    wid: str,
    cid: str,
    content: ConnectionConfiguration,
    correlation: str,
) -> ConfigurationView:
    preparation_workspace(db, actor, wid, mutation=True)
    require_admin(db, actor, fence=True)
    connection = available_connection(db, actor, wid, cid)
    try:
        validate_configuration_kind(content, connection.kind)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    template = None
    if content.notification and content.notification.template_asset_id:
        template = pin_asset(db, wid, str(content.notification.template_asset_id))
    environment = db.get(Environment, connection.environment_id)
    assert environment is not None
    version = (
        db.scalar(
            select(func.max(ConnectionConfigurationRecord.version)).where(
                ConnectionConfigurationRecord.workspace_id == wid,
                ConnectionConfigurationRecord.connection_id == cid,
            )
        )
        or 0
    ) + 1
    created_at = now()
    snapshot = ConfigurationSnapshot.model_validate(
        {
            "id": new_id(),
            "workspace_id": wid,
            "connection_id": cid,
            "version": version,
            "content": content,
            "digest": canonical_digest(content.model_dump(mode="json")),
            "connection_kind": connection.kind,
            "connection_name": connection.name,
            "environment_id": environment.id,
            "environment_name": environment.name,
            "template_asset": template,
            "created_by": actor.object_id,
            "created_at": utc(created_at),
        }
    )
    record = ConnectionConfigurationRecord(
        id=str(snapshot.id),
        workspace_id=wid,
        connection_id=cid,
        version=version,
        snapshot=canonical_json(snapshot.model_dump(mode="json")),
        created_by=actor.object_id,
        created_at=created_at,
        correlation_id=correlation,
    )
    db.add(record)
    db.flush()
    audit(db, actor, "configuration.registered", record.id, wid, version, correlation)
    return configuration_view(db, record)


def withdraw_configuration(
    db: Session,
    actor: Principal,
    wid: str,
    cid: str,
    config_id: str,
    version: int,
    correlation: str,
) -> ConfigurationView:
    preparation_workspace(db, actor, wid, mutation=True)
    require_admin(db, actor, fence=True)
    record = configuration_record(db, actor, wid, config_id)
    if record.connection_id != cid:
        raise HTTPException(404, "Configuration is unavailable in this workspace")
    if record.version != version:
        raise HTTPException(409, "Configuration version changed; reload before withdrawal")
    if withdrawal(db, config_id) is None:
        db.add(
            ConfigurationWithdrawal(
                configuration_id=config_id, actor=actor.object_id, correlation_id=correlation
            )
        )
        audit(db, actor, "configuration.withdrawn", config_id, wid, version, correlation)
        db.flush()
    return configuration_view(db, record)


def active_grant(db: Session, wid: str, oid: str) -> WorkspaceApproverGrant | None:
    return db.scalar(
        select(WorkspaceApproverGrant)
        .where(
            WorkspaceApproverGrant.workspace_id == wid,
            WorkspaceApproverGrant.object_id == oid,
            ~select(ApproverGrantRevocation.grant_id)
            .where(ApproverGrantRevocation.grant_id == WorkspaceApproverGrant.id)
            .with_hint(ApproverGrantRevocation, "WITH (HOLDLOCK)", "mssql")
            .exists(),
        )
        .with_hint(WorkspaceApproverGrant, "WITH (HOLDLOCK)", "mssql")
        .order_by(WorkspaceApproverGrant.granted_at.desc(), WorkspaceApproverGrant.id)
    )


def grant_view(grant: WorkspaceApproverGrant) -> ApproverGrantView:
    return ApproverGrantView(
        id=UUID(grant.id),
        workspace_id=UUID(grant.workspace_id),
        object_id=UUID(grant.object_id),
        granted_by=UUID(grant.granted_by),
        granted_at=utc(grant.granted_at),
    )


def list_approvers(db: Session, actor: Principal, wid: str) -> list[ApproverGrantView]:
    preparation_workspace(db, actor, wid)
    grants = db.scalars(
        select(WorkspaceApproverGrant)
        .where(
            WorkspaceApproverGrant.workspace_id == wid,
            ~select(ApproverGrantRevocation.grant_id)
            .where(ApproverGrantRevocation.grant_id == WorkspaceApproverGrant.id)
            .exists(),
        )
        .order_by(WorkspaceApproverGrant.object_id)
    )
    return [grant_view(grant) for grant in grants]


def grant_approver(
    db: Session, actor: Principal, wid: str, oid: str, correlation: str
) -> ApproverGrantView:
    preparation_workspace(db, actor, wid, mutation=True)
    require_admin(db, actor, fence=True)
    try:
        authorize(db, Principal(actor.tenant, oid), wid, fence=True)
    except HTTPException as exc:
        raise HTTPException(422, "The approver must already have current workspace access") from exc
    grant = active_grant(db, wid, oid)
    if grant is None:
        grant = WorkspaceApproverGrant(
            workspace_id=wid, object_id=oid, granted_by=actor.object_id, correlation_id=correlation
        )
        db.add(grant)
        db.flush()
        audit(db, actor, "approver.granted", grant.id, wid, correlation=correlation)
    return grant_view(grant)


def revoke_approver(db: Session, actor: Principal, wid: str, oid: str, correlation: str) -> None:
    preparation_workspace(db, actor, wid, mutation=True)
    require_admin(db, actor, fence=True)
    grant = active_grant(db, wid, oid)
    if grant is None:
        raise HTTPException(404, "Active approver grant not found")
    db.add(
        ApproverGrantRevocation(
            grant_id=grant.id, actor=actor.object_id, correlation_id=correlation
        )
    )
    audit(db, actor, "approver.revoked", grant.id, wid, correlation=correlation)


def board_record(
    db: Session,
    actor: Principal,
    wid: str,
    bid: str,
    role: str = "viewer",
    *,
    mutation: bool = False,
    version: int | None = None,
) -> PreparationBoard:
    preparation_workspace(db, actor, wid, role, mutation=mutation)
    board = db.scalar(
        select(PreparationBoard)
        .where(PreparationBoard.id == bid, PreparationBoard.workspace_id == wid)
        .with_hint(
            PreparationBoard, "WITH (UPDLOCK, HOLDLOCK)" if mutation else "WITH (HOLDLOCK)", "mssql"
        )
        .execution_options(populate_existing=True)
    )
    if board is None:
        raise HTTPException(404, "Board not found")
    if version is not None and version != board.version:
        raise HTTPException(
            409,
            "Board changed. Your unsaved work has not been overwritten; reload before retrying.",
        )
    return board


def origin_record(db: Session, board: PreparationBoard) -> BoardOrigin:
    origin = db.get(BoardOrigin, board.id)
    if origin is None:
        raise HTTPException(409, "Board provenance is unavailable")
    return origin


def contribute(db: Session, board: PreparationBoard, actor: Principal, correlation: str) -> None:
    if db.get(BoardContributor, (board.id, actor.object_id)) is None:
        db.add(
            BoardContributor(
                board_id=board.id,
                object_id=actor.object_id,
                first_version=board.version,
                correlation_id=correlation,
            )
        )


def bound_configurations(
    db: Session,
    actor: Principal,
    board: PreparationBoard,
    draft: BoardDraft,
    *,
    active: bool = True,
) -> list[ConfigurationSnapshot]:
    ids = sorted({str(step.binding.configuration_id) for step in draft.steps if step.binding})
    snapshots: list[ConfigurationSnapshot] = []
    for config_id in ids:
        record = configuration_record(db, actor, board.workspace_id, config_id, active=active)
        snapshot = ConfigurationSnapshot.model_validate_json(record.snapshot)
        if (
            snapshot.template_asset is not None
            and pin_asset(db, board.workspace_id, str(snapshot.template_asset.id))
            != snapshot.template_asset
        ):
            raise HTTPException(
                409, "The pinned notification asset metadata failed its integrity check"
            )
        snapshots.append(snapshot)
    return snapshots


def build_manifest(
    db: Session, actor: Principal, board: PreparationBoard, draft: BoardDraft
) -> PreparationManifest:
    origin = origin_record(db, board)
    assets = _ASSETS.validate_json(origin.assets)
    for asset in assets:
        if pin_asset(db, board.workspace_id, str(asset.id)) != asset:
            raise HTTPException(409, "Pinned published asset metadata failed its integrity check")
    try:
        return PreparationManifest(
            board_id=UUID(board.id),
            workspace_id=UUID(board.workspace_id),
            board_version=board.version,
            draft=draft,
            scenario=ScenarioPin.model_validate_json(origin.scenario),
            assets=assets,
            configurations=bound_configurations(db, actor, board, draft),
        )
    except ValueError as exc:
        raise HTTPException(
            422, "Invalid preparation operation bindings, parameter types or pinned references"
        ) from exc


def latest_preview(db: Session, board: PreparationBoard) -> PreparationPreview | None:
    return db.scalar(
        select(PreparationPreview)
        .where(PreparationPreview.board_id == board.id)
        .order_by(PreparationPreview.sequence.desc())
    )


def preview_view(board: PreparationBoard, preview: PreparationPreview) -> PreparationPreviewView:
    return PreparationPreviewView(
        id=UUID(preview.id),
        board_id=UUID(preview.board_id),
        board_version=preview.board_version,
        sequence=preview.sequence,
        digest=preview.digest,
        manifest=PreparationManifest.model_validate_json(preview.manifest),
        findings=_FINDINGS.validate_json(preview.findings),
        created_by=UUID(preview.created_by),
        created_at=utc(preview.created_at),
        is_current=preview.board_version == board.version,
    )


def require_preview_access(
    db: Session, actor: Principal, wid: str, preview: PreparationPreview
) -> None:
    manifest = PreparationManifest.model_validate_json(preview.manifest)
    for configuration in manifest.configurations:
        configuration_record(db, actor, wid, str(configuration.id))


def latest_approval(db: Session, board: PreparationBoard) -> PreparationApproval | None:
    return db.scalar(
        select(PreparationApproval)
        .where(PreparationApproval.board_id == board.id)
        .order_by(PreparationApproval.sequence.desc())
    )


def approval_revocation(db: Session, approval_id: str) -> ApprovalRevocation | None:
    return db.scalar(
        select(ApprovalRevocation)
        .where(ApprovalRevocation.approval_id == approval_id)
        .with_hint(ApprovalRevocation, "WITH (HOLDLOCK)", "mssql")
    )


def approval_validity(
    db: Session, actor: Principal, board: PreparationBoard, approval: PreparationApproval
) -> ApprovalValidity:
    reasons: list[str] = []
    if approval.decision != "approved":
        reasons.append("rejected")
    current = latest_approval(db, board)
    if current is None or current.id != approval.id:
        reasons.append("superseded")
    if approval_revocation(db, approval.id) is not None:
        reasons.append("revoked")
    if approval.expires_at <= now():
        reasons.append("expired")
    if approval.board_version != board.version:
        reasons.append("board_changed")
    grant = active_grant(db, board.workspace_id, approval.reviewer)
    if grant is None or grant.id != approval.grant_id:
        reasons.append("approver_grant_revoked")
    try:
        authorize(db, Principal(actor.tenant, approval.reviewer), board.workspace_id, fence=True)
    except HTTPException:
        reasons.append("workspace_access_removed")
    origin = origin_record(db, board)
    if (
        approval.reviewer == origin.created_by
        or db.get(BoardContributor, (board.id, approval.reviewer)) is not None
    ):
        reasons.append("self_approval")
    preview = db.get(PreparationPreview, approval.preview_id)
    if (
        preview is None
        or preview.board_id != board.id
        or preview.board_version != approval.board_version
        or preview.digest != approval.digest
        or canonical_digest(json.loads(preview.manifest)) != approval.digest
    ):
        reasons.append("preview_mismatch")
    else:
        manifest = PreparationManifest.model_validate_json(preview.manifest)
        try:
            current_draft = BoardDraft.model_validate_json(board.draft)
            if (
                canonical_digest(current_draft.model_dump(mode="json"))
                != canonical_digest(manifest.draft.model_dump(mode="json"))
                or board.name != manifest.draft.name
            ):
                reasons.append("board_changed")
        except ValueError:
            reasons.append("board_changed")
        for config in manifest.configurations:
            try:
                record = configuration_record(db, actor, board.workspace_id, str(config.id))
                if ConfigurationSnapshot.model_validate_json(record.snapshot) != config:
                    reasons.append("configuration_changed")
                if withdrawal(db, record.id) is not None:
                    reasons.append("configuration_withdrawn")
                if (
                    config.template_asset
                    and pin_asset(db, board.workspace_id, str(config.template_asset.id))
                    != config.template_asset
                ):
                    reasons.append("asset_changed")
            except HTTPException:
                reasons.append("configuration_unavailable")
        for asset in manifest.assets:
            try:
                if pin_asset(db, board.workspace_id, str(asset.id)) != asset:
                    reasons.append("asset_changed")
            except HTTPException:
                reasons.append("asset_unavailable")
    return ApprovalValidity(valid=not reasons, reasons=list(dict.fromkeys(reasons)))


def approval_view(
    db: Session, actor: Principal, board: PreparationBoard, approval: PreparationApproval
) -> PreparationApprovalView:
    revoked = approval_revocation(db, approval.id)
    return PreparationApprovalView.model_validate(
        {
            "id": approval.id,
            "board_id": board.id,
            "board_version": approval.board_version,
            "preview_id": approval.preview_id,
            "digest": approval.digest,
            "sequence": approval.sequence,
            "kind": approval.kind,
            "execution_authorized": approval.execution_authorized,
            "reviewer": approval.reviewer,
            "decision": approval.decision,
            "acknowledge_unverified": approval.acknowledge_unverified,
            "expires_at": utc(approval.expires_at),
            "note": approval.note,
            "created_at": utc(approval.created_at),
            "validity": approval_validity(db, actor, board, approval),
            "revoked_at": utc(revoked.created_at) if revoked else None,
            "revoked_by": revoked.actor if revoked else None,
        }
    )


def can_review(db: Session, actor: Principal, board: PreparationBoard) -> list[str]:
    reasons: list[str] = []
    if active_grant(db, board.workspace_id, actor.object_id) is None:
        reasons.append("explicit_approver_grant_required")
    if (
        origin_record(db, board).created_by == actor.object_id
        or db.get(BoardContributor, (board.id, actor.object_id)) is not None
    ):
        reasons.append("creator_or_contributor")
    return reasons


def board_view(db: Session, actor: Principal, board: PreparationBoard) -> BoardView:
    origin = origin_record(db, board)
    preview = latest_preview(db, board)
    approval = latest_approval(db, board)
    decision = approval_view(db, actor, board, approval) if approval else None
    status: Literal["none", "approved", "rejected", "invalid"] = "none"
    if decision is not None:
        status = (
            "rejected"
            if decision.decision == "rejected"
            else "approved"
            if decision.validity.valid
            else "invalid"
        )
    blockers = can_review(db, actor, board)
    if preview is not None:
        try:
            require_preview_access(db, actor, board.workspace_id, preview)
            snapshot = PreparationManifest.model_validate_json(preview.manifest)
            if any(withdrawal(db, str(config.id)) for config in snapshot.configurations):
                blockers.append("configuration_withdrawn")
        except HTTPException:
            # A board's own draft remains editable, but revoked inventory grants
            # must not disclose configuration contents through historical previews.
            preview = None
            blockers.append("preview_reference_unavailable")
    return BoardView(
        id=UUID(board.id),
        workspace_id=UUID(board.workspace_id),
        name=board.name,
        version=board.version,
        scenario_id=UUID(origin.scenario_id),
        revision_version=origin.revision_version,
        created_by=UUID(origin.created_by),
        created_at=utc(origin.created_at),
        updated_at=utc(board.updated_at),
        preparation_status="previewed"
        if preview and preview.board_version == board.version
        else "draft",
        approval_status=status,
        draft=BoardDraft.model_validate_json(board.draft),
        scenario=ScenarioPin.model_validate_json(origin.scenario),
        assets=_ASSETS.validate_json(origin.assets),
        contributors=[
            UUID(oid)
            for oid in db.scalars(
                select(BoardContributor.object_id)
                .where(BoardContributor.board_id == board.id)
                .order_by(BoardContributor.object_id)
            )
        ],
        latest_preview=preview_view(board, preview) if preview else None,
        current_approval=decision,
        can_edit=authorize(db, actor, board.workspace_id, fence=True) in {"editor", "owner"},
        can_approve=not blockers,
        approval_blockers=blockers,
    )


def list_boards(db: Session, actor: Principal, wid: str) -> list[BoardSummary]:
    preparation_workspace(db, actor, wid)
    return [
        BoardSummary.model_validate(
            board_view(db, actor, board).model_dump(include=set(BoardSummary.model_fields))
        )
        for board in db.scalars(
            select(PreparationBoard)
            .where(PreparationBoard.workspace_id == wid)
            .order_by(PreparationBoard.updated_at.desc(), PreparationBoard.id)
        )
    ]


def create_board(
    db: Session,
    actor: Principal,
    wid: str,
    body: BoardCreate,
    correlation: str,
) -> BoardView:
    preparation_workspace(db, actor, wid, "editor", mutation=True)
    scenario = db.scalar(
        select(Scenario)
        .where(Scenario.id == str(body.scenario_id), Scenario.workspace_id == wid)
        .with_hint(Scenario, "WITH (HOLDLOCK)", "mssql")
    )
    if scenario is None:
        raise HTTPException(404, "Scenario not found")
    revision = db.get(Revision, (scenario.id, body.revision_version))
    if revision is None:
        raise HTTPException(
            422, "Select an existing published revision; a mutable draft cannot create a board"
        )
    content = ScenarioContent.model_validate_json(revision.content)
    assets = [pin_asset(db, wid, str(aid)) for aid in content.asset_ids]
    draft = BoardDraft(name=body.name.strip())
    board = PreparationBoard(
        workspace_id=wid,
        name=draft.name,
        draft=canonical_json(draft.model_dump(mode="json")),
    )
    db.add(board)
    db.flush()
    db.add(
        BoardOrigin(
            board_id=board.id,
            scenario_id=scenario.id,
            revision_version=revision.version,
            scenario=ScenarioPin(
                scenario_id=UUID(scenario.id), revision_version=revision.version, content=content
            ).model_dump_json(),
            assets=_ASSETS.dump_json(assets).decode(),
            created_by=actor.object_id,
        )
    )
    contribute(db, board, actor, correlation)
    db.flush()
    audit(db, actor, "board.created", board.id, wid, board.version, correlation)
    return board_view(db, actor, board)


def save_board(
    db: Session,
    actor: Principal,
    wid: str,
    bid: str,
    draft: BoardDraft,
    version: int,
    correlation: str,
) -> BoardView:
    board = board_record(db, actor, wid, bid, "editor", mutation=True, version=version)
    # Validate even before freezing: missing values remain findings, but supplied
    # references, types and the separate operation graph must already be sound.
    origin = origin_record(db, board)
    try:
        validate_bindings(
            draft,
            bound_configurations(db, actor, board, draft),
            ScenarioPin.model_validate_json(origin.scenario),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    changed = db.execute(
        update(PreparationBoard)
        .where(
            PreparationBoard.id == board.id,
            PreparationBoard.workspace_id == wid,
            PreparationBoard.version == version,
        )
        .values(
            name=draft.name,
            draft=canonical_json(draft.model_dump(mode="json")),
            version=version + 1,
            updated_at=now(),
        )
        .returning(PreparationBoard.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if changed is None:
        raise HTTPException(409, "Board changed; your unsaved work has not been overwritten")
    db.refresh(board)
    contribute(db, board, actor, correlation)
    audit(db, actor, "board.saved", board.id, wid, board.version, correlation)
    db.flush()
    return board_view(db, actor, board)


def freeze_preview(
    db: Session, actor: Principal, wid: str, bid: str, version: int, correlation: str
) -> PreparationPreviewView:
    board = board_record(db, actor, wid, bid, "editor", mutation=True, version=version)
    manifest = build_manifest(db, actor, board, BoardDraft.model_validate_json(board.draft))
    previous = latest_preview(db, board)
    preview = PreparationPreview(
        board_id=board.id,
        board_version=board.version,
        sequence=previous.sequence + 1 if previous else 1,
        digest=canonical_digest(manifest.model_dump(mode="json")),
        manifest=canonical_json(manifest.model_dump(mode="json")),
        findings=_FINDINGS.dump_json(preparation_findings(manifest)).decode(),
        created_by=actor.object_id,
        correlation_id=correlation,
    )
    db.add(preview)
    contribute(db, board, actor, correlation)
    db.flush()
    audit(db, actor, "board.previewed", preview.id, wid, board.version, correlation)
    return preview_view(board, preview)


def list_previews(
    db: Session, actor: Principal, wid: str, bid: str
) -> list[PreparationPreviewView]:
    board = board_record(db, actor, wid, bid)
    previews = list(
        db.scalars(
            select(PreparationPreview)
            .where(PreparationPreview.board_id == board.id)
            .order_by(PreparationPreview.sequence.desc())
        )
    )
    for preview in previews:
        require_preview_access(db, actor, wid, preview)
    return [preview_view(board, preview) for preview in previews]


def decide_preparation(
    db: Session,
    actor: Principal,
    wid: str,
    bid: str,
    body: PreparationApprovalInput,
    version: int,
    correlation: str,
) -> PreparationApprovalView:
    board = board_record(db, actor, wid, bid, mutation=True, version=version)
    blockers = can_review(db, actor, board)
    if blockers:
        raise HTTPException(403, "A currently granted, independent workspace approver is required")
    grant = active_grant(db, wid, actor.object_id)
    assert grant is not None
    preview = db.get(PreparationPreview, str(body.preview_id))
    if preview is None or preview.board_id != board.id:
        raise HTTPException(404, "Preview not found")
    if preview.board_version != board.version or preview.digest != body.digest:
        raise HTTPException(409, "The preview or digest is stale; review a new preview")
    manifest = build_manifest(db, actor, board, BoardDraft.model_validate_json(board.draft))
    digest = canonical_digest(manifest.model_dump(mode="json"))
    if digest != preview.digest or canonical_digest(json.loads(preview.manifest)) != digest:
        raise HTTPException(409, "The pinned preparation changed; create and review a new preview")
    current_time = now()
    expires_at = body.expires_at.astimezone(UTC).replace(tzinfo=None)
    if not current_time < expires_at <= current_time + timedelta(days=30):
        raise HTTPException(422, "Choose an explicit future approval expiry within thirty days")
    previous = latest_approval(db, board)
    approval = PreparationApproval(
        board_id=board.id,
        board_version=board.version,
        preview_id=preview.id,
        digest=digest,
        sequence=previous.sequence + 1 if previous else 1,
        grant_id=grant.id,
        reviewer=actor.object_id,
        decision=body.decision,
        expires_at=expires_at,
        note=body.note,
        created_at=current_time,
        correlation_id=correlation,
    )
    db.add(approval)
    db.flush()
    audit(db, actor, f"preparation.{body.decision}", approval.id, wid, board.version, correlation)
    return approval_view(db, actor, board, approval)


def list_approvals(
    db: Session, actor: Principal, wid: str, bid: str
) -> list[PreparationApprovalView]:
    board = board_record(db, actor, wid, bid)
    return [
        approval_view(db, actor, board, approval)
        for approval in db.scalars(
            select(PreparationApproval)
            .where(PreparationApproval.board_id == board.id)
            .order_by(PreparationApproval.sequence.desc())
        )
    ]


def revoke_approval(
    db: Session,
    actor: Principal,
    wid: str,
    bid: str,
    approval_id: str,
    version: int,
    correlation: str,
) -> PreparationApprovalView:
    board = board_record(db, actor, wid, bid, mutation=True, version=version)
    approval = db.get(PreparationApproval, approval_id)
    if approval is None or approval.board_id != board.id:
        raise HTTPException(404, "Preparation decision not found")
    if approval.reviewer != actor.object_id and not is_admin(db, actor, fence=True):
        raise HTTPException(
            403, "Only the reviewer or an organization administrator can revoke a decision"
        )
    if approval_revocation(db, approval.id) is None:
        db.add(
            ApprovalRevocation(
                approval_id=approval.id,
                reason="explicit_revocation",
                actor=actor.object_id,
                correlation_id=correlation,
            )
        )
        audit(db, actor, "preparation.revoked", approval.id, wid, board.version, correlation)
        db.flush()
    return approval_view(db, actor, board, approval)


def revoke_reviewer_access(
    db: Session, actor: Principal, wid: str, oid: str, correlation: str
) -> None:
    """Called while membership removal holds the same workspace mutation fence."""
    approvals = db.scalars(
        select(PreparationApproval)
        .join(PreparationBoard, PreparationBoard.id == PreparationApproval.board_id)
        .where(PreparationBoard.workspace_id == wid, PreparationApproval.reviewer == oid)
    )
    for approval in approvals:
        if approval_revocation(db, approval.id) is None:
            db.add(
                ApprovalRevocation(
                    approval_id=approval.id,
                    reason="workspace_access_revoked",
                    actor=actor.object_id,
                    correlation_id=correlation,
                )
            )
            audit(
                db,
                actor,
                "preparation.access_revoked",
                approval.id,
                wid,
                approval.board_version,
                correlation,
            )
