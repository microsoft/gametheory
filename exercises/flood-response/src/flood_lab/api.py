from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from flood_lab import CONTRACT_VERSION
from flood_lab.auth import Actor, current_actor
from flood_lab.config import Settings, SetupRequired
from flood_lab.contracts import (
    MAX_WINDOW_SECONDS,
    AcknowledgeRequest,
    AcknowledgeRequestInput,
    AllocateRequest,
    AllocateRequestInput,
    CreateRequest,
    CreateRequestInput,
    ErrorView,
    EventList,
    MilestonesView,
    MutationView,
    OperationError,
    RequestList,
    RequestView,
    RunList,
    ShelterView,
    parse_version,
    version_value,
)
from flood_lab.database import make_engine, sessions
from flood_lab.service import LabService, OperationResponse

log = logging.getLogger("flood_lab")


def service(request: Request) -> LabService:
    if request.app.state.service is None:
        settings: Settings = request.app.state.settings
        settings.database()
        engine = make_engine(settings.database_url, settings.database_name)
        request.app.state.service = LabService(sessions(engine), settings.database_name)
    return request.app.state.service


ActorDependency = Annotated[Actor, Depends(current_actor)]
ServiceDependency = Annotated[LabService, Depends(service)]
Limit = Annotated[int, Query(ge=1, le=100)]
After = Annotated[int, Query(ge=0, le=9223372036854775806)]
AcknowledgeWithin = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_WINDOW_SECONDS,
        description="Inclusive acknowledgement deadline, in seconds after committed creation.",
    ),
]
AllocateWithin = Annotated[
    int,
    Query(
        ge=1,
        le=MAX_WINDOW_SECONDS,
        description="Inclusive full-allocation deadline, in seconds after committed creation.",
    ),
]
IdempotencyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Stable caller-generated key; reuse it with identical inputs to reconcile.",
    ),
]
IfMatchHeader = Annotated[
    str,
    Header(alias="If-Match", description="A single strong quoted ETag for the request's version."),
]


def result(response: OperationResponse) -> JSONResponse:
    headers = {"X-Correlation-ID": response.body["correlation_id"], "Cache-Control": "no-store"}
    if response.etag:
        headers["ETag"] = response.etag
    return JSONResponse(response.body, response.status, headers=headers)


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(
        title="Flood response lab — EXERCISE ONLY",
        version=CONTRACT_VERSION,
        description=(
            "Independent synthetic operational system. No Game Theory runtime, approval, reset "
            "or Graph-send endpoint. Scoped Entra access and database run grants are both required."
        ),
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings or Settings()
    app.state.service = None
    app.state.verifier = None
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app.state.settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "If-Match", "Idempotency-Key"],
        expose_headers=["ETag", "X-Correlation-ID"],
        max_age=600,
    )

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        request.state.correlation_id = uuid4()
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                oversized = int(content_length) > 16384
            except ValueError:
                oversized = True
            if oversized:
                return error_response(
                    request, OperationError(413, "request_too_large", "The request is too large.")
                )
        # Bound chunked bodies too; never buffer arbitrary input or log raw request bodies.
        if request.method == "POST":
            length, chunks = 0, []
            async for chunk in request.stream():
                length += len(chunk)
                if length > 16384:
                    return error_response(
                        request,
                        OperationError(413, "request_too_large", "The request is too large."),
                    )
                chunks.append(chunk)
            request._body = b"".join(chunks)
        try:
            response = await call_next(request)
        except Exception:
            # Prevent the ASGI server from logging a raw unexpected exception or its input values.
            log.error("lab_unexpected_error correlation_id=%s", request.state.correlation_id)
            response = error_response(
                request,
                OperationError(
                    500,
                    "outcome_unknown" if request.method == "POST" else "internal_error",
                    "The result could not be confirmed. Keep the correlation ID.",
                    "unknown" if request.method == "POST" else "failed",
                ),
            )
        response.headers.setdefault("X-Correlation-ID", str(request.state.correlation_id))
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def error_response(request: Request, error: OperationError) -> JSONResponse:
        body = ErrorView(
            outcome=error.outcome,
            code=error.code,
            message=error.message,
            correlation_id=getattr(request.state, "correlation_id", uuid4()),
        )
        return JSONResponse(
            body.model_dump(mode="json"),
            error.status,
            headers={"X-Correlation-ID": str(body.correlation_id), "Cache-Control": "no-store"},
        )

    @app.exception_handler(OperationError)
    async def operation_error(request: Request, error: OperationError):
        return error_response(request, error)

    @app.exception_handler(SetupRequired)
    async def setup_required(request: Request, error: SetupRequired):
        return error_response(
            request,
            OperationError(503, "setup_required", "Lab configuration is required.", "failed"),
        )

    @app.exception_handler(HTTPException)
    async def authentication_error(request: Request, error: HTTPException):
        response = error_response(
            request,
            OperationError(
                error.status_code, "authentication_required", "Sign in with lab access."
            ),
        )
        if error.headers:
            response.headers.update(error.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # Validation errors normally echo submitted values; deliberately do not serialize them.
        if any(
            issue["type"] == "missing"
            and len(issue["loc"]) == 2
            and issue["loc"][0] == "header"
            and str(issue["loc"][1]).lower() == "if-match"
            for issue in error.errors()
        ):
            return error_response(
                request,
                OperationError(
                    428, "precondition_required", "Read the record and send its If-Match."
                ),
            )
        return error_response(
            request,
            OperationError(422, "invalid_request", "Check the input types, bounds and UTC times."),
        )

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(TimeoutError)
    async def database_error(request: Request, error: Exception):
        outcome = "unknown" if request.method == "POST" else "failed"
        log.warning("lab_database_unavailable correlation_id=%s", request.state.correlation_id)
        return error_response(
            request,
            OperationError(
                503,
                "outcome_unknown" if outcome == "unknown" else "service_unavailable",
                (
                    "No confirmed result. Retry identical inputs with the same key to reconcile."
                    if outcome == "unknown"
                    else "The lab data service is unavailable."
                ),
                outcome,
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        log.error("lab_unexpected_error correlation_id=%s", request.state.correlation_id)
        return error_response(
            request,
            OperationError(
                500,
                "outcome_unknown" if request.method == "POST" else "internal_error",
                "The result could not be confirmed. Keep the correlation ID.",
                "unknown" if request.method == "POST" else "failed",
            ),
        )

    errors = {code: {"model": ErrorView} for code in (401, 403, 404, 409, 413, 422, 428, 500, 503)}

    @app.get("/health", tags=["setup"])
    def health():
        return {"contract_version": CONTRACT_VERSION, "exercise_only": True, "graph_sending": False}

    @app.get("/v1/runs", response_model=RunList, responses=errors)
    def list_runs(
        actor: ActorDependency,
        lab: ServiceDependency,
        limit: Annotated[int, Query(ge=1, le=50)] = 50,
        offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    ):
        return lab.list_runs(actor, limit, offset)

    @app.get("/v1/runs/{run_id}/shelters", response_model=list[ShelterView], responses=errors)
    def shelters(run_id: UUID, actor: ActorDependency, lab: ServiceDependency):
        return lab.shelters(actor, run_id)

    @app.get("/v1/runs/{run_id}/requests", response_model=RequestList, responses=errors)
    def list_requests(
        run_id: UUID,
        actor: ActorDependency,
        lab: ServiceDependency,
        limit: Limit = 50,
        after: After = 0,
        status: Literal["open", "acknowledged", "fulfilled"] | None = None,
    ):
        return lab.requests(actor, run_id, limit, after, status)

    @app.get(
        "/v1/runs/{run_id}/requests/{request_id}", response_model=RequestView, responses=errors
    )
    def get_request(run_id: UUID, request_id: UUID, actor: ActorDependency, lab: ServiceDependency):
        record = lab.request(actor, run_id, request_id)
        return JSONResponse(
            record.model_dump(mode="json"), headers={"ETag": f'"{record.record_version}"'}
        )

    @app.get(
        "/v1/runs/{run_id}/requests/{request_id}/milestones",
        response_model=MilestonesView,
        responses=errors,
    )
    def request_milestones(
        run_id: UUID,
        request_id: UUID,
        actor: ActorDependency,
        lab: ServiceDependency,
        acknowledge_within_seconds: AcknowledgeWithin,
        allocate_within_seconds: AllocateWithin,
    ):
        return lab.milestones(
            actor, run_id, request_id, acknowledge_within_seconds, allocate_within_seconds
        )

    @app.post(
        "/v1/runs/{run_id}/requests",
        response_model=MutationView,
        status_code=201,
        responses=errors,
    )
    def create_request(
        run_id: UUID,
        body: CreateRequestInput,
        request: Request,
        actor: ActorDependency,
        lab: ServiceDependency,
        idempotency_key: IdempotencyHeader,
    ):
        command = CreateRequest(**body.model_dump(), idempotency_key=idempotency_key)
        return result(lab.create_request(actor, run_id, command, request.state.correlation_id))

    @app.post(
        "/v1/runs/{run_id}/requests/{request_id}/acknowledge",
        response_model=MutationView,
        responses=errors,
    )
    def acknowledge(
        run_id: UUID,
        request_id: UUID,
        body: AcknowledgeRequestInput,
        request: Request,
        actor: ActorDependency,
        lab: ServiceDependency,
        idempotency_key: IdempotencyHeader,
        if_match: IfMatchHeader,
    ):
        command = AcknowledgeRequest(
            idempotency_key=idempotency_key,
            expected_version=version_value(parse_version(if_match)),
        )
        return result(
            lab.acknowledge(actor, run_id, request_id, command, request.state.correlation_id)
        )

    @app.post(
        "/v1/runs/{run_id}/requests/{request_id}/allocate",
        response_model=MutationView,
        responses=errors,
    )
    def allocate(
        run_id: UUID,
        request_id: UUID,
        body: AllocateRequestInput,
        request: Request,
        actor: ActorDependency,
        lab: ServiceDependency,
        idempotency_key: IdempotencyHeader,
        if_match: IfMatchHeader,
    ):
        command = AllocateRequest(
            **body.model_dump(),
            idempotency_key=idempotency_key,
            expected_version=version_value(parse_version(if_match)),
        )
        return result(
            lab.allocate(actor, run_id, request_id, command, request.state.correlation_id)
        )

    @app.get("/v1/runs/{run_id}/events", response_model=EventList, responses=errors)
    def events(
        run_id: UUID,
        actor: ActorDependency,
        lab: ServiceDependency,
        limit: Limit = 50,
        after: After = 0,
        record_id: UUID | None = None,
    ):
        return lab.events(actor, run_id, limit, after, record_id)

    return app


app = create_app()
