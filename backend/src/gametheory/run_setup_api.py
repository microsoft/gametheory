"""Operator-only API for reviewed run-check suggestions.

Requests are queued for the planning worker; nothing here creates, authorizes,
approves, starts, or dispatches a run.
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from gametheory.auth import Principal
from gametheory.config import get_settings
from gametheory.execution_service import grant_record, pinned_preparation
from gametheory.persistence import (
    PreparationBoard,
    PreparationPreview,
    RunSetupDispatchIntent,
    RunSetupRequest,
    timestamp,
)
from gametheory.preparation import PreparationManifest, canonical_digest
from gametheory.preparation_api import DB, Actor, restricted_json
from gametheory.preparation_service import board_record, require_preview_access
from gametheory.run_setup import (
    MAX_CONTEXT_CHARACTERS,
    RunCheckRequestAccepted,
    RunCheckRequestInput,
    RunCheckSuggestion,
    RunCheckSuggestionView,
    review_suggestion,
    run_check_context,
)
from gametheory.service import audit

router = APIRouter(
    prefix="/api/workspaces/{wid}", tags=["Run setup"], dependencies=[Depends(restricted_json)]
)
HISTORY_LIMIT = 20


def assistant_board(
    db: Session, actor: Principal, wid: str, bid: str, *, mutation: bool = False
) -> PreparationBoard:
    board = board_record(db, actor, wid, bid, mutation=mutation)
    if grant_record(db, wid, actor.object_id, "operator") is None:
        raise HTTPException(403, "An explicit execution operator grant is required")
    if not get_settings().run_assistant_enabled:
        raise HTTPException(
            503, "The run-check assistant is not configured. The run setup forms remain available."
        )
    return board


def accessible_manifest(
    db: Session, actor: Principal, wid: str, preview: PreparationPreview | None
) -> PreparationManifest | None:
    """The pinned preview, only while its configurations remain available to this caller."""

    if preview is None:
        return None
    try:
        require_preview_access(db, actor, wid, preview)
    except HTTPException:
        return None
    if canonical_digest(json.loads(preview.manifest)) != preview.digest:
        return None
    return PreparationManifest.model_validate_json(preview.manifest)


def suggestion_view(
    db: Session,
    actor: Principal,
    wid: str,
    board: PreparationBoard,
    record: RunSetupRequest,
    manifests: dict[str, PreparationManifest | None],
) -> RunCheckSuggestionView:
    preview = db.get(PreparationPreview, record.preview_id)
    if record.preview_id not in manifests:
        manifests[record.preview_id] = accessible_manifest(db, actor, wid, preview)
    manifest = manifests[record.preview_id]
    view: dict[str, object] = {
        "id": record.id,
        "preview_id": record.preview_id,
        "prompt": record.prompt,
        "status": record.status,
        "error": record.error,
        "created_at": timestamp(record.created_at),
        "summary": None,
        "observations": [],
        "objectives": [],
        "recovery": [],
        "questions": [],
        "is_current": preview is not None
        and preview.board_id == board.id
        and preview.board_version == board.version
        and preview.digest == record.preview_digest,
    }
    if record.status != "proposed" or not record.suggestion:
        return RunCheckSuggestionView.model_validate(view)
    try:
        suggestion = RunCheckSuggestion.model_validate_json(record.suggestion)
    except ValidationError:
        view["error"] = "This stored suggestion can no longer be read. Request a new one."
        return RunCheckSuggestionView.model_validate(view)
    if manifest is None or preview is None or preview.digest != record.preview_digest:
        # Do not disclose content derived from configurations the caller cannot see.
        view["error"] = "This suggestion's preview is no longer available to you."
        return RunCheckSuggestionView.model_validate(view)
    review = review_suggestion(manifest, suggestion)
    view |= {
        "summary": suggestion.summary,
        "questions": suggestion.questions,
        "observations": review.observations,
        "objectives": review.objectives,
        "recovery": review.recovery,
    }
    return RunCheckSuggestionView.model_validate(view)


@router.get("/boards/{bid}/run-setup/suggestions", response_model=list[RunCheckSuggestionView])
def suggestions(wid: UUID, bid: UUID, db: DB, actor: Actor) -> list[RunCheckSuggestionView]:
    """The board's latest suggestion requests, newest first, each reviewed against its preview."""

    board = assistant_board(db, actor, str(wid), str(bid))
    records = db.scalars(
        select(RunSetupRequest)
        .where(RunSetupRequest.board_id == board.id, RunSetupRequest.workspace_id == str(wid))
        .order_by(RunSetupRequest.created_at.desc(), RunSetupRequest.id.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    manifests: dict[str, PreparationManifest | None] = {}
    return [suggestion_view(db, actor, str(wid), board, record, manifests) for record in records]


@router.post(
    "/boards/{bid}/run-setup/suggestions",
    response_model=RunCheckRequestAccepted,
    status_code=202,
)
def request_suggestions(
    wid: UUID,
    bid: UUID,
    body: RunCheckRequestInput,
    db: DB,
    actor: Actor,
    request: Request,
) -> RunCheckRequestAccepted:
    """Queue a request for suggested run checks. It never creates or changes a run."""

    board = assistant_board(db, actor, str(wid), str(bid), mutation=True)
    existing = db.get(RunSetupRequest, str(body.request_id))
    if existing:
        if (
            existing.workspace_id,
            existing.board_id,
            existing.actor,
            existing.prompt,
            existing.preview_id,
            existing.preview_digest,
        ) != (
            str(wid),
            board.id,
            actor.object_id,
            body.prompt,
            str(body.preview_id),
            body.preview_digest,
        ):
            raise HTTPException(409, "Request identifier is already in use")
        return RunCheckRequestAccepted.model_validate(
            {"id": existing.id, "status": existing.status}
        )
    preparation = pinned_preparation(db, actor, str(wid), board, body)
    if (
        db.scalar(
            select(RunSetupRequest.id).where(
                RunSetupRequest.board_id == board.id,
                RunSetupRequest.status.in_(["queued", "running"]),
            )
        )
        is not None
    ):
        raise HTTPException(
            409, "This board already has an active suggestion request. Wait for it to finish."
        )
    context = run_check_context(preparation).model_dump_json()
    if len(context) > MAX_CONTEXT_CHARACTERS:
        raise HTTPException(
            422, "This preparation is too large for the run-check assistant. Use the forms."
        )
    pending = RunSetupRequest(
        id=str(body.request_id),
        board_id=board.id,
        workspace_id=str(wid),
        preview_id=str(body.preview_id),
        preview_digest=body.preview_digest,
        actor=actor.object_id,
        prompt=body.prompt,
        context=context,
        status="queued",
    )
    db.add(pending)
    db.flush()
    db.add(RunSetupDispatchIntent(request_id=pending.id))
    audit(
        db,
        actor,
        "run_setup.suggestion_requested",
        pending.id,
        str(wid),
        correlation=request.state.correlation,
    )
    return RunCheckRequestAccepted.model_validate({"id": pending.id, "status": "queued"})
