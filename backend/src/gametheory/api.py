import hashlib
import logging
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.base import RequestResponseEndpoint
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gametheory.assets import put_blob, read_blob, validate_upload
from gametheory.auth import Principal, authenticate, authorize, is_admin, require_admin
from gametheory.config import get_settings
from gametheory.domain import (
    CommentInput,
    ConnectionInput,
    MembershipInput,
    Named,
    PlanningInput,
    ProposalContent,
    ScenarioContent,
    ScenarioView,
    WorkspaceView,
    mermaid,
)
from gametheory.logging import configure_logging
from gametheory.persistence import (
    Asset,
    Comment,
    Connection,
    ConnectionGrant,
    DispatchIntent,
    Environment,
    Membership,
    PlanningRequest,
    Revision,
    Scenario,
    Workspace,
    get_db,
    new_id,
    timestamp,
)
from gametheory.service import (
    audit,
    expected_version,
    get_scenario,
    list_connections,
    save_scenario,
    scenario_view,
    validate_references,
)

logger = logging.getLogger(__name__)
configure_logging()
DB = Annotated[Session, Depends(get_db, scope="function")]
Actor = Annotated[Principal, Depends(authenticate)]
Match = Annotated[str | None, Header(alias="If-Match")]
app = FastAPI(title="Game Theory", version="0.1.0", docs_url=None, redoc_url=None)


class BodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        size = 0

        async def bounded_receive() -> Message:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > get_settings().max_upload_bytes + 65536:
                    raise HTTPException(413, "Request exceeds the configured upload limit")
            return message

        await self.app(scope, bounded_receive, send)


app.add_middleware(BodyLimit)


@app.middleware("http")
async def headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
    request.state.correlation = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.correlation
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' blob: data:; font-src 'self'; "
            f"connect-src 'self' {get_settings().profile.authority}; "
            f"frame-src {get_settings().profile.authority}; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error(
        "SQL operation failed",
        extra={
            "correlation_id": request.state.correlation,
            "error_type": type(exc).__name__,
        },
    )
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Application database is unavailable. Retry after checking service health.",
            "request_id": request.state.correlation,
        },
    )


@app.get("/api/config")
def config() -> dict[str, object]:
    settings = get_settings()
    return {
        "cloud": settings.cloud,
        "auth": {
            "configured": settings.auth_configured,
            "client_id": settings.spa_client_id,
            "authority": f"{settings.profile.authority}/{settings.tenant_id}",
            "scope": settings.api_scope,
        },
        "capabilities": {
            "authoring": bool(settings.sql_url),
            "assets": bool(settings.blob_url or settings.blob_connection_string),
            "planning": settings.planning_enabled,
            "execution": False,
        },
        "max_upload_bytes": settings.max_upload_bytes,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/api/me")
def me(actor: Actor, db: DB) -> dict[str, object]:
    return {"object_id": actor.object_id, "organization_admin": is_admin(db, actor)}


@app.get("/api/workspaces", response_model=list[WorkspaceView])
def workspaces(actor: Actor, db: DB) -> list[WorkspaceView]:
    query = select(Workspace).where(Workspace.organization_id == actor.tenant)
    admin = is_admin(db, actor)
    if not admin:
        query = query.join(Membership).where(Membership.object_id == actor.object_id)
    return [
        WorkspaceView(id=w.id, name=w.name, role=authorize(db, actor, w.id))
        for w in db.scalars(query.order_by(Workspace.name, Workspace.id))
    ]


@app.post("/api/workspaces", response_model=WorkspaceView, status_code=201)
def create_workspace(body: Named, actor: Actor, db: DB, request: Request) -> WorkspaceView:
    require_admin(db, actor)
    workspace = Workspace(organization_id=actor.tenant, name=body.name.strip())
    db.add(workspace)
    db.flush()
    audit(
        db,
        actor,
        "workspace.created",
        workspace.id,
        workspace.id,
        correlation=request.state.correlation,
    )
    return WorkspaceView(id=workspace.id, name=workspace.name, role="owner")


@app.get("/api/workspaces/{wid}/members")
def members(wid: str, actor: Actor, db: DB) -> list[dict[str, str]]:
    authorize(db, actor, wid, "owner")
    return [
        {"object_id": row.object_id, "role": row.role}
        for row in db.scalars(select(Membership).where(Membership.workspace_id == wid))
    ]


@app.put("/api/workspaces/{wid}/members")
def set_member(
    wid: str, body: MembershipInput, actor: Actor, db: DB, request: Request
) -> dict[str, str]:
    authorize(db, actor, wid, "owner")
    object_id = str(body.object_id)
    if object_id == actor.object_id and not is_admin(db, actor):
        raise HTTPException(422, "Ask another owner or administrator to change your own role")
    db.merge(Membership(workspace_id=wid, object_id=object_id, role=body.role))
    audit(db, actor, "membership.set", object_id, wid, correlation=request.state.correlation)
    return {"object_id": object_id, "role": body.role}


@app.delete("/api/workspaces/{wid}/members/{oid}", status_code=204)
def remove_member(wid: str, oid: str, actor: Actor, db: DB, request: Request) -> None:
    authorize(db, actor, wid, "owner")
    if oid == actor.object_id:
        raise HTTPException(422, "Ask another owner or administrator to remove your membership")
    member = db.get(Membership, (wid, oid))
    if member is None:
        raise HTTPException(404, "Membership not found")
    db.delete(member)
    audit(db, actor, "membership.removed", oid, wid, correlation=request.state.correlation)


@app.get("/api/environments")
def environments(actor: Actor, db: DB) -> list[dict[str, str]]:
    return [
        {"id": e.id, "name": e.name}
        for e in db.scalars(
            select(Environment)
            .where(Environment.organization_id == actor.tenant)
            .order_by(Environment.name)
        )
    ]


@app.post("/api/environments", status_code=201)
def create_environment(body: Named, actor: Actor, db: DB, request: Request) -> dict[str, str]:
    require_admin(db, actor)
    env = Environment(organization_id=actor.tenant, name=body.name.strip())
    db.add(env)
    db.flush()
    audit(db, actor, "environment.created", env.id, correlation=request.state.correlation)
    return {"id": env.id, "name": env.name}


@app.get("/api/workspaces/{wid}/connections")
def connections(wid: str, actor: Actor, db: DB) -> list[dict[str, str]]:
    authorize(db, actor, wid)
    return [
        {
            "id": c.id,
            "name": c.name,
            "kind": c.kind,
            "scope": c.scope,
            "environment_id": c.environment_id,
            "description": c.description,
            "status": "inventory_only",
        }
        for c in list_connections(db, actor, wid)
    ]


@app.post("/api/workspaces/{wid}/connections", status_code=201)
def create_connection(
    wid: str,
    body: ConnectionInput,
    actor: Actor,
    db: DB,
    request: Request,
) -> dict[str, str]:
    authorize(db, actor, wid, "editor")
    if body.scope != "workspace":
        require_admin(db, actor)
    env = db.get(Environment, str(body.environment_id))
    if env is None or env.organization_id != actor.tenant:
        raise HTTPException(422, "Environment unavailable")
    if body.scope == "assigned" and not body.workspace_ids:
        raise HTTPException(422, "Select at least one assigned workspace")
    if body.scope != "assigned" and body.workspace_ids:
        raise HTTPException(422, "Workspace assignments apply only to assigned connections")
    for target in set(body.workspace_ids):
        authorize(db, actor, str(target), "owner")
    connection = Connection(
        organization_id=actor.tenant,
        workspace_id=wid if body.scope == "workspace" else None,
        environment_id=str(body.environment_id),
        name=body.name,
        kind=body.kind,
        scope=body.scope,
        description=body.description,
    )
    db.add(connection)
    db.flush()
    for target in set(body.workspace_ids):
        db.add(ConnectionGrant(connection_id=connection.id, workspace_id=str(target)))
    audit(
        db,
        actor,
        "connection.inventory_created",
        connection.id,
        wid,
        correlation=request.state.correlation,
    )
    return {"id": connection.id, "status": "inventory_only"}


@app.get("/api/workspaces/{wid}/scenarios", response_model=list[ScenarioView])
def scenarios(wid: str, actor: Actor, db: DB) -> list[ScenarioView]:
    authorize(db, actor, wid)
    return [
        scenario_view(s)
        for s in db.scalars(
            select(Scenario)
            .where(Scenario.workspace_id == wid)
            .order_by(Scenario.updated_at.desc())
        )
    ]


@app.post("/api/workspaces/{wid}/scenarios", response_model=ScenarioView, status_code=201)
def create_scenario(wid: str, body: Named, actor: Actor, db: DB, request: Request) -> ScenarioView:
    authorize(db, actor, wid, "editor")
    scenario = Scenario(
        workspace_id=wid, content=ScenarioContent(title=body.name).model_dump_json()
    )
    db.add(scenario)
    db.flush()
    audit(db, actor, "scenario.created", scenario.id, wid, 1, request.state.correlation)
    return scenario_view(scenario)


@app.get("/api/workspaces/{wid}/scenarios/{sid}", response_model=ScenarioView)
def read_scenario(wid: str, sid: str, actor: Actor, db: DB, response: Response) -> ScenarioView:
    scenario = get_scenario(db, actor, wid, sid)
    response.headers["ETag"] = f'"{scenario.version}"'
    return scenario_view(scenario)


@app.put("/api/workspaces/{wid}/scenarios/{sid}", response_model=ScenarioView)
def write_scenario(
    wid: str,
    sid: str,
    body: ScenarioContent,
    actor: Actor,
    db: DB,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> ScenarioView:
    scenario = get_scenario(db, actor, wid, sid, "editor")
    result = save_scenario(
        db, actor, scenario, body, expected_version(if_match), request.state.correlation
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@app.get("/api/workspaces/{wid}/scenarios/{sid}/mermaid")
def diagram(wid: str, sid: str, actor: Actor, db: DB) -> dict[str, str]:
    scenario = get_scenario(db, actor, wid, sid)
    return {"source": mermaid(ScenarioContent.model_validate_json(scenario.content))}


@app.post("/api/workspaces/{wid}/scenarios/{sid}/revisions", status_code=201)
def publish(
    wid: str,
    sid: str,
    actor: Actor,
    db: DB,
    request: Request,
    if_match: Match = None,
) -> dict[str, object]:
    scenario = get_scenario(db, actor, wid, sid, "editor")
    version = expected_version(if_match)
    # A conditional no-op update locks the exact draft through snapshot publication.
    count = db.execute(
        update(Scenario)
        .where(Scenario.id == sid, Scenario.version == version)
        .values(version=version)
        .returning(Scenario.id)
    ).scalar_one_or_none()
    if count is None:
        raise HTTPException(409, "Draft changed; reload before publishing")
    db.refresh(scenario)
    content = ScenarioContent.model_validate_json(scenario.content)
    validate_references(db, actor, wid, content)
    errors = content.publication_errors()
    if errors:
        raise HTTPException(422, "; ".join(errors))
    existing = db.get(Revision, (sid, version))
    if existing is None:
        db.add(
            Revision(
                scenario_id=sid, version=version, content=scenario.content, actor=actor.object_id
            )
        )
        audit(db, actor, "scenario.published", sid, wid, version, request.state.correlation)
    return {"version": version, "execution_approved": False}


@app.get("/api/workspaces/{wid}/scenarios/{sid}/revisions")
def revisions(wid: str, sid: str, actor: Actor, db: DB) -> list[dict[str, object]]:
    get_scenario(db, actor, wid, sid)
    return [
        {
            "version": r.version,
            "actor": r.actor,
            "created_at": timestamp(r.created_at),
            "content": ScenarioContent.model_validate_json(r.content).model_dump(mode="json"),
        }
        for r in db.scalars(
            select(Revision).where(Revision.scenario_id == sid).order_by(Revision.version.desc())
        )
    ]


@app.get("/api/workspaces/{wid}/scenarios/{sid}/comments")
def comments(wid: str, sid: str, actor: Actor, db: DB) -> list[dict[str, object]]:
    get_scenario(db, actor, wid, sid)
    return [
        {
            "id": c.id,
            "body": c.body,
            "base_version": c.base_version,
            "actor": c.actor,
            "created_at": timestamp(c.created_at),
        }
        for c in db.scalars(
            select(Comment)
            .where(Comment.scenario_id == sid)
            .order_by(Comment.created_at, Comment.id)
        )
    ]


@app.post("/api/workspaces/{wid}/scenarios/{sid}/comments", status_code=201)
def create_comment(
    wid: str,
    sid: str,
    body: CommentInput,
    actor: Actor,
    db: DB,
    request: Request,
) -> dict[str, str]:
    scenario = get_scenario(db, actor, wid, sid, "editor")
    if body.base_version > scenario.version:
        raise HTTPException(422, "Comment references a future draft")
    comment = Comment(scenario_id=sid, actor=actor.object_id, **body.model_dump())
    db.add(comment)
    db.flush()
    audit(
        db, actor, "comment.created", comment.id, wid, body.base_version, request.state.correlation
    )
    return {"id": comment.id}


def asset_view(asset: Asset) -> dict[str, object]:
    return {
        "id": asset.id,
        "name": asset.name,
        "media_type": asset.media_type,
        "sha256": asset.sha256,
        "size": asset.size,
        "state": asset.state,
        "previous_id": asset.previous_id,
        "actor": asset.actor,
        "created_at": timestamp(asset.created_at),
    }


@app.get("/api/workspaces/{wid}/assets")
def assets(wid: str, actor: Actor, db: DB) -> list[dict[str, object]]:
    authorize(db, actor, wid)
    return [
        asset_view(a)
        for a in db.scalars(
            select(Asset).where(Asset.workspace_id == wid).order_by(Asset.created_at.desc())
        )
    ]


@app.post("/api/workspaces/{wid}/assets", status_code=201)
def upload_asset(
    wid: str,
    file: UploadFile,
    actor: Actor,
    db: DB,
    request: Request,
    previous_id: str | None = None,
) -> dict[str, object]:
    authorize(db, actor, wid, "editor")
    name = (file.filename or "").replace("\\", "/").split("/")[-1]
    if not name.strip() or len(name) > 160 or any(ord(c) < 32 for c in name):
        raise HTTPException(422, "A valid filename of at most 160 characters is required")
    if previous_id:
        previous = db.get(Asset, previous_id)
        if previous is None or previous.workspace_id != wid or previous.state != "ready":
            raise HTTPException(422, "Previous asset version is unavailable")
    data = file.file.read(get_settings().max_upload_bytes + 1)
    if len(data) > get_settings().max_upload_bytes:
        raise HTTPException(413, "Asset exceeds the configured upload limit")
    media = file.content_type or "application/octet-stream"
    digest = validate_upload(data, media)
    aid = new_id()
    asset = Asset(
        id=aid,
        workspace_id=wid,
        previous_id=previous_id,
        name=name,
        media_type=media,
        blob_key=f"{wid}/{aid}",
        sha256=digest,
        size=len(data),
        actor=actor.object_id,
        state="staged",
    )
    db.add(asset)
    db.flush()
    audit(db, actor, "asset.staged", aid, wid, correlation=request.state.correlation)
    # Persist the intent before crossing the SQL/Blob boundary.
    db.commit()
    put_blob(asset.blob_key, data, media)
    # Reauthorize after upload; revocation cannot publish a usable asset.
    authorize(db, actor, wid, "editor")
    published = db.execute(
        update(Asset)
        .where(Asset.id == aid, Asset.state == "staged")
        .values(state="ready")
        .returning(Asset.id)
    ).scalar_one_or_none()
    if published is None:
        raise HTTPException(409, "Upload was retired before finalization; upload a new asset")
    audit(db, actor, "asset.created", aid, wid, correlation=request.state.correlation)
    db.commit()
    return asset_view(asset)


@app.get("/api/workspaces/{wid}/assets/{aid}/content")
def download_asset(wid: str, aid: str, actor: Actor, db: DB, request: Request) -> Response:
    authorize(db, actor, wid)
    asset = db.get(Asset, aid)
    if asset is None or asset.workspace_id != wid or asset.state != "ready":
        raise HTTPException(404, "Asset is unavailable")
    data = read_blob(asset.blob_key, max_bytes=asset.size)
    if len(data) != asset.size or hashlib.sha256(data).hexdigest() != asset.sha256:
        logger.error(
            "Asset integrity verification failed",
            extra={
                "correlation_id": request.state.correlation,
                "blob_key": asset.blob_key,
            },
        )
        raise HTTPException(
            409, "Stored asset integrity check failed; this version cannot be served"
        )
    return Response(
        data,
        media_type=asset.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(asset.name)}",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@app.get("/api/workspaces/{wid}/scenarios/{sid}/planning")
def planning(wid: str, sid: str, actor: Actor, db: DB) -> list[dict[str, object]]:
    get_scenario(db, actor, wid, sid)
    return [
        {
            "id": p.id,
            "prompt": p.prompt,
            "actor": p.actor,
            "base_version": p.base_version,
            "status": p.status,
            "error": p.error,
            "created_at": timestamp(p.created_at),
            "proposal": ProposalContent.model_validate_json(p.proposal).model_dump(mode="json")
            if p.proposal
            else None,
        }
        for p in db.scalars(
            select(PlanningRequest)
            .where(PlanningRequest.scenario_id == sid)
            .order_by(PlanningRequest.created_at, PlanningRequest.id)
        )
    ]


@app.post("/api/workspaces/{wid}/scenarios/{sid}/planning", status_code=202)
def request_plan(
    wid: str,
    sid: str,
    body: PlanningInput,
    actor: Actor,
    db: DB,
    request: Request,
) -> dict[str, str]:
    scenario = get_scenario(db, actor, wid, sid, "editor")
    if not get_settings().planning_enabled:
        raise HTTPException(
            503, "AI planning is not configured. Manual authoring remains available."
        )
    existing = db.get(PlanningRequest, str(body.request_id))
    if existing:
        if (existing.scenario_id, existing.actor, existing.prompt, existing.base_version) != (
            sid,
            actor.object_id,
            body.prompt,
            body.base_version,
        ):
            raise HTTPException(409, "Request identifier is already in use")
        return {"id": existing.id, "status": existing.status}
    locked = db.execute(
        update(Scenario)
        .where(Scenario.id == sid, Scenario.version == body.base_version)
        .values(version=body.base_version)
        .returning(Scenario.id)
    ).scalar_one_or_none()
    if locked is None:
        raise HTTPException(409, "Save/reload the draft before requesting a proposal")
    existing = db.get(PlanningRequest, str(body.request_id))
    if existing:
        if (existing.scenario_id, existing.actor, existing.prompt, existing.base_version) != (
            sid,
            actor.object_id,
            body.prompt,
            body.base_version,
        ):
            raise HTTPException(409, "Request identifier is already in use")
        return {"id": existing.id, "status": existing.status}
    if (
        db.scalar(
            select(PlanningRequest.id).where(
                PlanningRequest.scenario_id == sid,
                PlanningRequest.status.in_(["queued", "running"]),
            )
        )
        is not None
    ):
        raise HTTPException(409, "This scenario already has an active planning request")
    content = ScenarioContent.model_validate_json(scenario.content)
    validate_references(db, actor, wid, content)
    pending = PlanningRequest(
        id=str(body.request_id),
        scenario_id=sid,
        actor=actor.object_id,
        prompt=body.prompt,
        base_version=body.base_version,
        context=scenario.content,
    )
    db.add(pending)
    db.flush()
    db.add(DispatchIntent(request_id=pending.id))
    audit(
        db,
        actor,
        "planning.requested",
        pending.id,
        wid,
        body.base_version,
        request.state.correlation,
    )
    return {"id": pending.id, "status": "queued"}


@app.post("/api/workspaces/{wid}/scenarios/{sid}/planning/{pid}/{decision}")
def decide_proposal(
    wid: str,
    sid: str,
    pid: str,
    decision: str,
    actor: Actor,
    db: DB,
    request: Request,
    if_match: Match = None,
) -> dict[str, object]:
    scenario = get_scenario(db, actor, wid, sid, "editor")
    if decision not in {"apply", "reject"}:
        raise HTTPException(404, "Unknown proposal decision")
    pending = db.get(PlanningRequest, pid)
    if pending is None or pending.scenario_id != sid:
        raise HTTPException(404, "Proposal not found")
    changed = db.execute(
        update(PlanningRequest)
        .where(PlanningRequest.id == pid, PlanningRequest.status == "proposed")
        .values(status="applied" if decision == "apply" else "rejected")
        .returning(PlanningRequest.id)
    ).scalar_one_or_none()
    if changed is None or not pending.proposal:
        raise HTTPException(409, "Proposal is not awaiting review")
    result: ScenarioView | None = None
    if decision == "apply":
        version = expected_version(if_match)
        if version != pending.base_version:
            raise HTTPException(409, "Proposal is stale; request a new proposal")
        proposal = ProposalContent.model_validate_json(pending.proposal)
        result = save_scenario(
            db, actor, scenario, proposal.content, version, request.state.correlation
        )
    audit(
        db, actor, f"proposal.{decision}", pid, wid, pending.base_version, request.state.correlation
    )
    return {
        "status": "applied" if result else "rejected",
        "scenario": result.model_dump(mode="json") if result else None,
    }


dist = Path(get_settings().web_dist)
if (dist / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="static-assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str) -> FileResponse:
    if path == "api" or path.startswith("api/") or not (dist / "index.html").is_file():
        raise HTTPException(404, "Build the frontend or use the Vite development server")
    return FileResponse(dist / "index.html")
