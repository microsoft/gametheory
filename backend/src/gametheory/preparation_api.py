from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from gametheory import preparation_service as service
from gametheory.auth import Principal, authenticate
from gametheory.persistence import get_db
from gametheory.preparation import (
    ApproverGrantInput,
    ApproverGrantView,
    BoardCreate,
    BoardDraft,
    BoardSummary,
    BoardView,
    ConfigurationView,
    ConnectionConfiguration,
    ExecutionDisabled,
    PreparationApprovalInput,
    PreparationApprovalView,
    PreparationPreviewView,
    strict_json,
)
from gametheory.service import audit, expected_version

DB = Annotated[Session, Depends(get_db, scope="function")]
Actor = Annotated[Principal, Depends(authenticate)]
Match = Annotated[str | None, Header(alias="If-Match")]
VERSION_HEADER = {
    "description": "Strong numeric resource version for If-Match.",
    "schema": {"type": "string", "pattern": r'^"[1-9][0-9]*"$'},
}
RESOURCE_HEADERS = {"ETag": VERSION_HEADER}


async def restricted_json(request: Request) -> None:
    data = await request.body()
    if data:
        try:
            strict_json(data)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(
                422, "Preparation JSON must be finite and have unique object keys"
            ) from exc


router = APIRouter(
    prefix="/api/workspaces/{wid}",
    tags=["Preparation"],
    dependencies=[Depends(authenticate), Depends(restricted_json)],
)


@router.get(
    "/connections/{cid}/configurations",
    response_model=list[ConfigurationView],
)
def configurations(wid: UUID, cid: UUID, db: DB, actor: Actor) -> list[ConfigurationView]:
    return service.list_configurations(db, actor, str(wid), str(cid))


@router.post(
    "/connections/{cid}/configurations",
    response_model=ConfigurationView,
    status_code=201,
    responses={201: {"headers": RESOURCE_HEADERS}},
)
def register_configuration(
    wid: UUID,
    cid: UUID,
    body: ConnectionConfiguration,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
) -> ConfigurationView:
    result = service.register_configuration(
        db,
        actor,
        str(wid),
        str(cid),
        body,
        request.state.correlation,
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.post(
    "/connections/{cid}/configurations/{config_id}/withdraw",
    response_model=ConfigurationView,
    responses={200: {"headers": RESOURCE_HEADERS}},
)
def withdraw_configuration(
    wid: UUID,
    cid: UUID,
    config_id: UUID,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> ConfigurationView:
    result = service.withdraw_configuration(
        db,
        actor,
        str(wid),
        str(cid),
        str(config_id),
        expected_version(if_match),
        request.state.correlation,
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.get("/approvers", response_model=list[ApproverGrantView])
def approvers(wid: UUID, db: DB, actor: Actor) -> list[ApproverGrantView]:
    return service.list_approvers(db, actor, str(wid))


@router.put("/approvers", response_model=ApproverGrantView)
def grant_approver(
    wid: UUID,
    body: ApproverGrantInput,
    db: DB,
    actor: Actor,
    request: Request,
) -> ApproverGrantView:
    result = service.grant_approver(
        db,
        actor,
        str(wid),
        str(body.object_id),
        request.state.correlation,
    )
    return result


@router.delete("/approvers/{object_id}", status_code=204)
def revoke_approver(
    wid: UUID,
    object_id: UUID,
    db: DB,
    actor: Actor,
    request: Request,
) -> None:
    service.revoke_approver(
        db,
        actor,
        str(wid),
        str(object_id),
        request.state.correlation,
    )


@router.get("/boards", response_model=list[BoardSummary])
def boards(wid: UUID, db: DB, actor: Actor) -> list[BoardSummary]:
    return service.list_boards(db, actor, str(wid))


@router.post(
    "/boards",
    response_model=BoardView,
    status_code=201,
    responses={201: {"headers": RESOURCE_HEADERS}},
)
def create_board(
    wid: UUID,
    body: BoardCreate,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
) -> BoardView:
    result = service.create_board(
        db,
        actor,
        str(wid),
        body,
        request.state.correlation,
    )
    response.headers["ETag"] = f'"{result.version}"'
    response.headers["Location"] = f"/api/workspaces/{wid}/boards/{result.id}"
    return result


@router.get(
    "/boards/{bid}", response_model=BoardView, responses={200: {"headers": RESOURCE_HEADERS}}
)
def board(wid: UUID, bid: UUID, db: DB, actor: Actor, response: Response) -> BoardView:
    record = service.board_record(db, actor, str(wid), str(bid))
    response.headers["ETag"] = f'"{record.version}"'
    return service.board_view(db, actor, record)


@router.put(
    "/boards/{bid}", response_model=BoardView, responses={200: {"headers": RESOURCE_HEADERS}}
)
def save_board(
    wid: UUID,
    bid: UUID,
    body: BoardDraft,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> BoardView:
    result = service.save_board(
        db,
        actor,
        str(wid),
        str(bid),
        body,
        expected_version(if_match),
        request.state.correlation,
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.post(
    "/boards/{bid}/previews",
    response_model=PreparationPreviewView,
    status_code=201,
    responses={201: {"headers": RESOURCE_HEADERS}},
)
def freeze_preview(
    wid: UUID,
    bid: UUID,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> PreparationPreviewView:
    result = service.freeze_preview(
        db,
        actor,
        str(wid),
        str(bid),
        expected_version(if_match),
        request.state.correlation,
    )
    response.headers["ETag"] = f'"{result.board_version}"'
    return result


@router.get(
    "/boards/{bid}/previews",
    response_model=list[PreparationPreviewView],
    responses={200: {"headers": RESOURCE_HEADERS}},
)
def previews(
    wid: UUID, bid: UUID, db: DB, actor: Actor, response: Response
) -> list[PreparationPreviewView]:
    record = service.board_record(db, actor, str(wid), str(bid))
    response.headers["ETag"] = f'"{record.version}"'
    return service.list_previews(db, actor, str(wid), str(bid))


@router.post(
    "/boards/{bid}/approvals",
    response_model=PreparationApprovalView,
    status_code=201,
    responses={201: {"headers": RESOURCE_HEADERS}},
)
def decide_preparation(
    wid: UUID,
    bid: UUID,
    body: PreparationApprovalInput,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> PreparationApprovalView:
    result = service.decide_preparation(
        db,
        actor,
        str(wid),
        str(bid),
        body,
        expected_version(if_match),
        request.state.correlation,
    )
    response.headers["ETag"] = f'"{result.board_version}"'
    return result


@router.get(
    "/boards/{bid}/approvals",
    response_model=list[PreparationApprovalView],
    responses={200: {"headers": RESOURCE_HEADERS}},
)
def approvals(
    wid: UUID, bid: UUID, db: DB, actor: Actor, response: Response
) -> list[PreparationApprovalView]:
    record = service.board_record(db, actor, str(wid), str(bid))
    response.headers["ETag"] = f'"{record.version}"'
    return service.list_approvals(db, actor, str(wid), str(bid))


@router.post(
    "/boards/{bid}/approvals/{approval_id}/revoke",
    response_model=PreparationApprovalView,
    responses={200: {"headers": RESOURCE_HEADERS}},
)
def revoke_approval(
    wid: UUID,
    bid: UUID,
    approval_id: UUID,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> PreparationApprovalView:
    version = expected_version(if_match)
    result = service.revoke_approval(
        db,
        actor,
        str(wid),
        str(bid),
        str(approval_id),
        version,
        request.state.correlation,
    )
    response.headers["ETag"] = f'"{version}"'
    return result


@router.post(
    "/boards/{bid}/execute",
    status_code=501,
    response_model=ExecutionDisabled,
    responses={
        501: {
            "model": ExecutionDisabled,
            "description": "Execution is permanently disabled",
            "headers": RESOURCE_HEADERS,
        }
    },
)
def execute_disabled(
    wid: UUID,
    bid: UUID,
    db: DB,
    actor: Actor,
    request: Request,
    if_match: Match = None,
) -> JSONResponse:
    record = service.board_record(
        db, actor, str(wid), str(bid), "editor", version=expected_version(if_match)
    )
    audit(
        db,
        actor,
        "execution.disabled",
        record.id,
        str(wid),
        record.version,
        request.state.correlation,
    )
    return JSONResponse(
        status_code=501,
        content=ExecutionDisabled().model_dump(mode="json"),
        headers={"ETag": f'"{record.version}"'},
    )
