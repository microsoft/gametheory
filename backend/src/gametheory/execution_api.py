from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from gametheory import execution_service as service
from gametheory.execution import (
    Capability,
    ExecutionGrantInput,
    ExecutionGrantView,
    ManualRecoveryInput,
    RunApprovalInput,
    RunControl,
    RunCreate,
    RunPreflightView,
    RunSummary,
    RunView,
)
from gametheory.persistence import ExerciseRun, ExerciseRunState, timestamp
from gametheory.preparation_api import DB, RESOURCE_HEADERS, Actor, Match, restricted_json
from gametheory.preparation_service import board_record
from gametheory.service import audit, expected_version

router = APIRouter(
    prefix="/api/workspaces/{wid}", tags=["Execution"], dependencies=[Depends(restricted_json)]
)


@router.get("/execution-grants", response_model=list[ExecutionGrantView])
def grants(wid: UUID, db: DB, actor: Actor) -> list[ExecutionGrantView]:
    return service.list_grants(db, actor, str(wid))


@router.put("/execution-grants", response_model=ExecutionGrantView)
def grant(
    wid: UUID, body: ExecutionGrantInput, db: DB, actor: Actor, request: Request
) -> ExecutionGrantView:
    return service.set_grant(db, actor, str(wid), body, request.state.correlation)


@router.delete("/execution-grants/{oid}/{capability}", status_code=204)
def revoke_grant(
    wid: UUID, oid: UUID, capability: Capability, db: DB, actor: Actor, request: Request
) -> None:
    service.revoke_grant(db, actor, str(wid), str(oid), capability, request.state.correlation)


@router.get("/boards/{bid}/runs", response_model=list[RunSummary])
def runs(wid: UUID, bid: UUID, db: DB, actor: Actor) -> list[RunSummary]:
    board_record(db, actor, str(wid), str(bid))
    records = db.execute(
        select(ExerciseRun, ExerciseRunState)
        .join(ExerciseRunState, ExerciseRunState.run_id == ExerciseRun.id)
        .where(ExerciseRun.board_id == str(bid), ExerciseRun.workspace_id == str(wid))
        .order_by(ExerciseRun.created_at.desc())
        .limit(50)
    ).all()
    return [
        RunSummary.model_validate(
            {
                "id": record.id,
                "board_id": record.board_id,
                "state": state.state,
                "phase": state.phase,
                "operator": record.operator,
                "created_at": timestamp(record.created_at),
            }
        )
        for record, state in records
    ]


@router.post("/boards/{bid}/runs/preflight", response_model=RunPreflightView)
def preflight_run(
    wid: UUID, bid: UUID, body: RunCreate, db: DB, actor: Actor, if_match: Match = None
) -> RunPreflightView:
    """Check a proposed run without creating, authorizing, or dispatching anything."""
    return service.preflight_run(
        db, actor, str(wid), str(bid), body, expected_version(if_match, "board")
    )


@router.post(
    "/boards/{bid}/runs",
    response_model=RunView,
    status_code=201,
    responses={201: {"headers": RESOURCE_HEADERS}},
)
def create_run(
    wid: UUID,
    bid: UUID,
    body: RunCreate,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> RunView:
    result = service.create_run(
        db,
        actor,
        str(wid),
        str(bid),
        body,
        expected_version(if_match, "board"),
        request.state.correlation,
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.get("/runs/{rid}", response_model=RunView, responses={200: {"headers": RESOURCE_HEADERS}})
def run(wid: UUID, rid: UUID, db: DB, actor: Actor, response: Response) -> RunView:
    result = service.run_view(db, actor, *service.load_run(db, actor, str(wid), str(rid)))
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.post(
    "/runs/{rid}/controls", response_model=RunView, responses={200: {"headers": RESOURCE_HEADERS}}
)
def control(
    wid: UUID,
    rid: UUID,
    body: RunControl,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> RunView:
    record, state, manifest = service.load_run(
        db, actor, str(wid), str(rid), mutation=True, version=expected_version(if_match, "run")
    )
    service.control_run(db, actor, record, state, manifest, body)
    audit(
        db,
        actor,
        f"execution.{body.action}",
        record.id,
        str(wid),
        version=state.version,
        correlation=request.state.correlation,
    )
    db.flush()
    result = service.run_view(db, actor, record, state, manifest)
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.post(
    "/runs/{rid}/approvals", response_model=RunView, responses={200: {"headers": RESOURCE_HEADERS}}
)
def approve(
    wid: UUID,
    rid: UUID,
    body: RunApprovalInput,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> RunView:
    record, state, manifest = service.load_run(
        db, actor, str(wid), str(rid), mutation=True, version=expected_version(if_match, "run")
    )
    service.decide_run(db, actor, record, state, manifest, body)
    audit(
        db,
        actor,
        "execution.reviewed",
        record.id,
        str(wid),
        version=state.version,
        correlation=request.state.correlation,
    )
    db.flush()
    result = service.run_view(db, actor, record, state, manifest)
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.post(
    "/runs/{rid}/approvals/{aid}/revoke",
    response_model=RunView,
    responses={200: {"headers": RESOURCE_HEADERS}},
)
def revoke_approval(
    wid: UUID,
    rid: UUID,
    aid: UUID,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> RunView:
    record, state, manifest = service.load_run(
        db, actor, str(wid), str(rid), mutation=True, version=expected_version(if_match, "run")
    )
    service.revoke_approval(db, actor, record, state, str(aid))
    audit(
        db,
        actor,
        "execution.approval_revoked",
        record.id,
        str(wid),
        correlation=request.state.correlation,
    )
    db.flush()
    result = service.run_view(db, actor, record, state, manifest)
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.post("/runs/{rid}/manual-recovery-reports", response_model=RunView)
def manual_report(
    wid: UUID,
    rid: UUID,
    body: ManualRecoveryInput,
    db: DB,
    actor: Actor,
    request: Request,
    if_match: Match = None,
) -> RunView:
    record, state, manifest = service.load_run(
        db, actor, str(wid), str(rid), mutation=True, version=expected_version(if_match, "run")
    )
    service.manual_recovery(db, actor, record, state, body)
    audit(
        db,
        actor,
        "execution.manual_recovery_report",
        record.id,
        str(wid),
        correlation=request.state.correlation,
    )
    db.flush()
    return service.run_view(db, actor, record, state, manifest)
