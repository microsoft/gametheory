import asyncio
import logging
import signal
import threading
from collections.abc import Generator
from datetime import timedelta
from typing import Any

import grpc
from agent_framework.exceptions import AgentFrameworkException
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from durabletask import task
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.azuremanaged.worker import DurableTaskSchedulerWorker
from durabletask.client import OrchestrationStatus
from durabletask.internal.orchestrator_service_pb2 import OrchestrationIdReusePolicy
from fastapi import HTTPException
from openai import APIError
from pydantic import ValidationError
from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from gametheory.auth import Principal, authorize
from gametheory.config import get_settings
from gametheory.domain import ProposalContent, ScenarioContent
from gametheory.logging import configure_logging
from gametheory.persistence import (
    DispatchIntent,
    PlanningRequest,
    RunSetupDispatchIntent,
    RunSetupRequest,
    Scenario,
    Workspace,
    new_id,
    now,
    session_factory,
)
from gametheory.planning import InvalidRunCheckOutput, generate_proposal, generate_run_checks
from gametheory.run_setup import RunCheckSuggestion
from gametheory.service import audit, validate_references

logger = logging.getLogger(__name__)


def plan_v1(ctx: task.OrchestrationContext, request_id: str) -> Generator[task.Task[Any], Any, str]:
    try:
        yield ctx.call_activity(
            propose_v1,
            input=request_id,
            retry_policy=task.RetryPolicy(
                first_retry_interval=timedelta(seconds=5),
                max_number_of_attempts=3,
                backoff_coefficient=2,
                max_retry_interval=timedelta(seconds=30),
                retry_timeout=timedelta(minutes=10),
            ),
        )
    except task.TaskFailedError:
        yield ctx.call_activity(
            fail_v1,
            input=request_id,
            retry_policy=task.RetryPolicy(
                first_retry_interval=timedelta(seconds=10),
                max_number_of_attempts=5,
            ),
        )
    return request_id


def fail_request(request_id: str, reason: str) -> None:
    with session_factory().begin() as db:
        pending = db.get(PlanningRequest, request_id)
        if pending and pending.status in {"queued", "running"}:
            pending.status = "failed"
            pending.error = reason
            pending.finished_at = now()
            scenario = db.get(Scenario, pending.scenario_id)
            workspace = db.get(Workspace, scenario.workspace_id) if scenario else None
            if workspace:
                audit(
                    db,
                    Principal(workspace.organization_id, pending.actor),
                    "planning.failed",
                    pending.id,
                    workspace.id,
                    correlation=pending.id,
                )


planning_orchestrator: task.Orchestrator[str, str] = plan_v1


def fail_v1(_ctx: task.ActivityContext, request_id: str) -> None:
    fail_request(
        request_id, "Planning failed after bounded retries. Submit a new request to retry."
    )


def propose_v1(_ctx: task.ActivityContext, request_id: str) -> str:
    try:
        return create_proposal(request_id)
    except (
        AgentFrameworkException,
        SQLAlchemyError,
        AzureError,
        APIError,
        ValidationError,
        HTTPException,
        TimeoutError,
    ) as exc:
        error_type = type(exc).__name__
    logger.error(
        "Planning activity failed", extra={"request_id": request_id, "error_type": error_type}
    )
    # Raise outside the handler so SDK failure history cannot capture provider bodies.
    raise RuntimeError(f"Planning activity failed ({error_type}); request {request_id}")


def create_proposal(request_id: str) -> str:
    with session_factory().begin() as db:
        pending = db.get(PlanningRequest, request_id)
        if pending is None:
            raise ValueError("Unknown planning request")
        if pending.status not in {"queued", "running"}:
            return request_id
        scenario = db.get(Scenario, pending.scenario_id)
        workspace = db.get(Workspace, scenario.workspace_id) if scenario else None
        if workspace is None:
            raise ValueError("Planning workspace no longer exists")
        actor = Principal(workspace.organization_id, pending.actor)
        try:
            authorize(db, actor, workspace.id, "editor")
            validate_references(
                db, actor, workspace.id, ScenarioContent.model_validate_json(pending.context)
            )
        except HTTPException:
            pending.status = "failed"
            pending.error = "Planning context is no longer authorized or available."
            pending.finished_at = now()
            audit(db, actor, "planning.denied", request_id, workspace.id, correlation=request_id)
            return request_id
        pending.status = "running"
        pending.error = None
        context, prompt, workspace_id = pending.context, pending.prompt, workspace.id
        previous = db.scalars(
            select(PlanningRequest)
            .where(
                PlanningRequest.scenario_id == pending.scenario_id,
                PlanningRequest.created_at < pending.created_at,
            )
            .order_by(PlanningRequest.created_at.desc())
            .limit(12)
        ).all()
        history = [
            {
                "user": p.prompt,
                "status": p.status,
                "assistant": ProposalContent.model_validate_json(p.proposal).summary
                if p.proposal
                else p.status,
            }
            for p in reversed(previous)
        ]
    proposal = asyncio.run(generate_proposal(context, prompt, history))
    with session_factory().begin() as db:
        authorize(db, actor, workspace_id, "editor")
        validate_references(db, actor, workspace_id, proposal.content)
        # A repeated activity can incur a model call, but cannot publish its result twice.
        completed = db.execute(
            update(PlanningRequest)
            .where(
                PlanningRequest.id == request_id,
                PlanningRequest.status.in_(["queued", "running"]),
            )
            .values(
                status="proposed",
                proposal=proposal.model_dump_json(),
                error=None,
                finished_at=now(),
            )
            .returning(PlanningRequest.id)
        ).scalar_one_or_none()
        if completed:
            audit(db, actor, "planning.proposed", request_id, workspace_id, correlation=request_id)
    return request_id


def dispatch_once(client: DurableTaskSchedulerClient) -> None:
    with session_factory().begin() as db:
        ids = list(
            db.scalars(
                select(DispatchIntent.request_id)
                .where(
                    DispatchIntent.next_attempt <= now(),
                    or_(
                        DispatchIntent.state == "pending",
                        ((DispatchIntent.state == "leased") & (DispatchIntent.lease_until < now())),
                    ),
                )
                .order_by(DispatchIntent.next_attempt)
                .limit(20)
            )
        )
    for request_id in ids:
        token = new_id()
        with session_factory().begin() as db:
            claimed = db.execute(
                update(DispatchIntent)
                .where(
                    DispatchIntent.request_id == request_id,
                    or_(
                        DispatchIntent.state == "pending",
                        ((DispatchIntent.state == "leased") & (DispatchIntent.lease_until < now())),
                    ),
                )
                .values(
                    state="leased",
                    lease_token=token,
                    lease_until=now() + timedelta(minutes=2),
                    attempts=DispatchIntent.attempts + 1,
                )
                .returning(DispatchIntent.request_id)
            ).scalar_one_or_none()
            if claimed is None:
                continue
            pending = db.get(PlanningRequest, request_id)
            terminal = pending is None or pending.status not in {"queued", "running"}
        try:
            if not terminal:
                instance_id = f"planning-{request_id}"
                state = client.get_orchestration_state(instance_id, fetch_payloads=False)
                if state is None:
                    client.schedule_new_orchestration(
                        planning_orchestrator,
                        instance_id=instance_id,
                        input=request_id,
                        reuse_id_policy=OrchestrationIdReusePolicy(replaceableStatus=[]),
                    )
            with session_factory().begin() as db:
                db.execute(
                    update(DispatchIntent)
                    .where(
                        DispatchIntent.request_id == request_id,
                        DispatchIntent.lease_token == token,
                    )
                    .values(state="scheduled", last_error=None)
                )
                db.execute(
                    update(PlanningRequest)
                    .where(
                        PlanningRequest.id == request_id,
                        PlanningRequest.status == "queued",
                    )
                    .values(error=None)
                )
        except (grpc.RpcError, AzureError) as exc:
            logger.error(
                "Scheduler dispatch failed",
                extra={
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                },
            )
            with session_factory().begin() as db:
                intent = db.get(DispatchIntent, request_id)
                if intent and intent.lease_token == token:
                    intent.state = "pending"
                    intent.next_attempt = now() + timedelta(
                        seconds=min(300, 2 ** min(intent.attempts, 8))
                    )
                    intent.last_error = "Scheduler dispatch unavailable; retry is scheduled."
                    pending = db.get(PlanningRequest, request_id)
                    if pending and pending.status == "queued":
                        pending.error = intent.last_error


def reconcile_once(client: DurableTaskSchedulerClient) -> None:
    with session_factory().begin() as db:
        ids = list(
            db.scalars(
                select(PlanningRequest.id)
                .join(DispatchIntent)
                .where(
                    PlanningRequest.status.in_(["queued", "running"]),
                    DispatchIntent.state == "scheduled",
                )
                .order_by(PlanningRequest.created_at)
                .limit(100)
            )
        )
    for request_id in ids:
        state = client.get_orchestration_state(f"planning-{request_id}", fetch_payloads=False)
        if state is None:
            with session_factory().begin() as db:
                pending = db.get(PlanningRequest, request_id)
                if pending and pending.status in {"queued", "running"}:
                    pending.error = (
                        "Scheduler history is unavailable; outcome is uncertain. "
                        "An operator must inspect this request before retrying."
                    )
            logger.warning("Scheduler history unavailable", extra={"request_id": request_id})
            continue
        if state and state.runtime_status in {
            OrchestrationStatus.FAILED,
            OrchestrationStatus.TERMINATED,
            OrchestrationStatus.COMPLETED,
        }:
            fail_request(
                request_id, "Workflow ended without a proposal. Submit a new request to retry."
            )


ACTIVE = ("queued", "running")
RUN_CHECK_OUTPUT_ERROR = (
    "The assistant's answer did not match the run-check format, so nothing was suggested. "
    "Try again, or rephrase the request."
)
RUN_CHECK_DENIED = "Run-check context is no longer authorized or available."


def suggest_run_checks_v1(
    ctx: task.OrchestrationContext, request_id: str
) -> Generator[task.Task[Any], Any, str]:
    try:
        yield ctx.call_activity(
            propose_run_checks_v1,
            input=request_id,
            retry_policy=task.RetryPolicy(
                first_retry_interval=timedelta(seconds=5),
                max_number_of_attempts=3,
                backoff_coefficient=2,
                max_retry_interval=timedelta(seconds=30),
                retry_timeout=timedelta(minutes=10),
            ),
        )
    except task.TaskFailedError:
        yield ctx.call_activity(
            fail_run_checks_v1,
            input=request_id,
            retry_policy=task.RetryPolicy(
                first_retry_interval=timedelta(seconds=10),
                max_number_of_attempts=5,
            ),
        )
    return request_id


run_check_orchestrator: task.Orchestrator[str, str] = suggest_run_checks_v1


def run_check_instance(request_id: str) -> str:
    return f"run-setup-{request_id}"


def close_run_check(
    db: Session, request_id: str, reason: str, operation: str = "run_setup.failed"
) -> None:
    """Fail an active request once; proposed and failed requests are never reopened."""

    closed = db.execute(
        update(RunSetupRequest)
        .where(RunSetupRequest.id == request_id, RunSetupRequest.status.in_(ACTIVE))
        .values(status="failed", error=reason, finished_at=now())
        .returning(RunSetupRequest.workspace_id, RunSetupRequest.actor)
    ).first()
    workspace = db.get(Workspace, closed.workspace_id) if closed else None
    if closed and workspace:
        audit(
            db,
            Principal(workspace.organization_id, closed.actor),
            operation,
            request_id,
            workspace.id,
            correlation=request_id,
        )


def fail_run_check(request_id: str, reason: str) -> None:
    with session_factory().begin() as db:
        close_run_check(db, request_id, reason)


def fail_run_checks_v1(_ctx: task.ActivityContext, request_id: str) -> None:
    fail_run_check(
        request_id, "Suggestion failed after bounded retries. Send a new request to retry."
    )


def propose_run_checks_v1(_ctx: task.ActivityContext, request_id: str) -> str:
    try:
        return create_run_check_suggestion(request_id)
    except (
        AgentFrameworkException,
        SQLAlchemyError,
        AzureError,
        APIError,
        ValidationError,
        HTTPException,
        TimeoutError,
    ) as exc:
        error_type = type(exc).__name__
    logger.error(
        "Run-check activity failed", extra={"request_id": request_id, "error_type": error_type}
    )
    # Raise outside the handler so SDK failure history cannot capture provider bodies.
    raise RuntimeError(f"Run-check activity failed ({error_type}); request {request_id}")


def is_member(db: Session, actor: Principal, workspace_id: str) -> bool:
    # Membership only: this worker cannot read execution grants. The API rechecks
    # operator authority whenever suggestions are listed or linked to a run.
    try:
        authorize(db, actor, workspace_id)
    except HTTPException:
        return False
    return True


def suggestion_summary(record: RunSetupRequest) -> str:
    if record.suggestion:
        try:
            return RunCheckSuggestion.model_validate_json(record.suggestion).summary
        except ValidationError:
            pass
    return record.status


def create_run_check_suggestion(request_id: str) -> str:
    with session_factory().begin() as db:
        pending = db.get(RunSetupRequest, request_id)
        if pending is None:
            raise ValueError("Unknown run-check request")
        if pending.status not in ACTIVE:
            return request_id
        workspace = db.get(Workspace, pending.workspace_id)
        if workspace is None:
            raise ValueError("Run-check workspace no longer exists")
        actor = Principal(workspace.organization_id, pending.actor)
        workspace_id, context, prompt = workspace.id, pending.context, pending.prompt
        if not is_member(db, actor, workspace_id):
            close_run_check(db, request_id, RUN_CHECK_DENIED, "run_setup.denied")
            return request_id
        previous = db.scalars(
            select(RunSetupRequest)
            .where(
                RunSetupRequest.board_id == pending.board_id,
                RunSetupRequest.created_at < pending.created_at,
            )
            .order_by(RunSetupRequest.created_at.desc())
            .limit(6)
        ).all()
        history = [
            {"user": item.prompt, "status": item.status, "assistant": suggestion_summary(item)}
            for item in reversed(previous)
        ]
        started = db.execute(
            update(RunSetupRequest)
            .where(RunSetupRequest.id == request_id, RunSetupRequest.status.in_(ACTIVE))
            .values(status="running", error=None)
            .returning(RunSetupRequest.id)
        ).scalar_one_or_none()
        if started is None:
            return request_id
    try:
        suggestion = asyncio.run(generate_run_checks(context, prompt, history))
    except InvalidRunCheckOutput:
        logger.warning("Run-check output rejected", extra={"request_id": request_id})
        fail_run_check(request_id, RUN_CHECK_OUTPUT_ERROR)
        return request_id
    with session_factory().begin() as db:
        if not is_member(db, actor, workspace_id):
            close_run_check(db, request_id, RUN_CHECK_DENIED, "run_setup.denied")
            return request_id
        # A repeated activity can incur a model call, but cannot publish its result twice.
        completed = db.execute(
            update(RunSetupRequest)
            .where(RunSetupRequest.id == request_id, RunSetupRequest.status.in_(ACTIVE))
            .values(
                status="proposed",
                suggestion=suggestion.model_dump_json(),
                error=None,
                finished_at=now(),
            )
            .returning(RunSetupRequest.id)
        ).scalar_one_or_none()
        if completed:
            audit(
                db, actor, "run_setup.suggested", request_id, workspace_id, correlation=request_id
            )
    return request_id


def _run_check_claimable() -> Any:
    return or_(
        RunSetupDispatchIntent.state == "pending",
        (RunSetupDispatchIntent.state == "leased") & (RunSetupDispatchIntent.lease_until < now()),
    )


def dispatch_run_checks_once(client: DurableTaskSchedulerClient) -> None:
    with session_factory().begin() as db:
        ids = list(
            db.scalars(
                select(RunSetupDispatchIntent.request_id)
                .where(RunSetupDispatchIntent.next_attempt <= now(), _run_check_claimable())
                .order_by(RunSetupDispatchIntent.next_attempt)
                .limit(20)
            )
        )
    for request_id in ids:
        token = new_id()
        with session_factory().begin() as db:
            claimed = db.execute(
                update(RunSetupDispatchIntent)
                .where(RunSetupDispatchIntent.request_id == request_id, _run_check_claimable())
                .values(
                    state="leased",
                    lease_token=token,
                    lease_until=now() + timedelta(minutes=2),
                    attempts=RunSetupDispatchIntent.attempts + 1,
                )
                .returning(RunSetupDispatchIntent.request_id)
            ).scalar_one_or_none()
            if claimed is None:
                continue
            pending = db.get(RunSetupRequest, request_id)
            terminal = pending is None or pending.status not in ACTIVE
        try:
            if not terminal:
                instance_id = run_check_instance(request_id)
                state = client.get_orchestration_state(instance_id, fetch_payloads=False)
                if state is None:
                    client.schedule_new_orchestration(
                        run_check_orchestrator,
                        instance_id=instance_id,
                        input=request_id,
                        reuse_id_policy=OrchestrationIdReusePolicy(replaceableStatus=[]),
                    )
            with session_factory().begin() as db:
                db.execute(
                    update(RunSetupDispatchIntent)
                    .where(
                        RunSetupDispatchIntent.request_id == request_id,
                        RunSetupDispatchIntent.lease_token == token,
                    )
                    .values(state="scheduled", last_error=None)
                )
                db.execute(
                    update(RunSetupRequest)
                    .where(RunSetupRequest.id == request_id, RunSetupRequest.status == "queued")
                    .values(error=None)
                )
        except (grpc.RpcError, AzureError) as exc:
            logger.error(
                "Run-check dispatch failed",
                extra={"request_id": request_id, "error_type": type(exc).__name__},
            )
            with session_factory().begin() as db:
                intent = db.get(RunSetupDispatchIntent, request_id)
                if intent and intent.lease_token == token:
                    intent.state = "pending"
                    intent.next_attempt = now() + timedelta(
                        seconds=min(300, 2 ** min(intent.attempts, 8))
                    )
                    intent.last_error = "Scheduler dispatch unavailable; retry is scheduled."
                    pending = db.get(RunSetupRequest, request_id)
                    if pending and pending.status == "queued":
                        pending.error = intent.last_error


def reconcile_run_checks_once(client: DurableTaskSchedulerClient) -> None:
    with session_factory().begin() as db:
        ids = list(
            db.scalars(
                select(RunSetupRequest.id)
                .join(RunSetupDispatchIntent)
                .where(
                    RunSetupRequest.status.in_(ACTIVE),
                    RunSetupDispatchIntent.state == "scheduled",
                )
                .order_by(RunSetupRequest.created_at)
                .limit(100)
            )
        )
    for request_id in ids:
        state = client.get_orchestration_state(run_check_instance(request_id), fetch_payloads=False)
        if state is None:
            # Suggestions have no external effects, so a lost history closes the request
            # instead of blocking the board; a late result still cannot publish.
            logger.warning("Scheduler history unavailable", extra={"request_id": request_id})
            fail_run_check(
                request_id, "Scheduler history is unavailable. Send a new request to retry."
            )
        elif state.runtime_status in {
            OrchestrationStatus.FAILED,
            OrchestrationStatus.TERMINATED,
            OrchestrationStatus.COMPLETED,
        }:
            fail_run_check(
                request_id, "Workflow ended without a suggestion. Send a new request to retry."
            )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = get_settings()
    if not settings.planning_enabled:
        raise SystemExit("GT_PLANNING_ENABLED must be true and all runtime settings configured")
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    credential = (
        None
        if settings.scheduler_emulator
        else DefaultAzureCredential(authority=settings.profile.authority)
    )
    configure_logging()
    client = DurableTaskSchedulerClient(
        host_address=settings.scheduler_endpoint,
        taskhub=settings.scheduler_taskhub,
        token_credential=credential,
        secure_channel=not settings.scheduler_emulator,
    )
    worker = DurableTaskSchedulerWorker(
        host_address=settings.scheduler_endpoint,
        taskhub=settings.scheduler_taskhub,
        token_credential=credential,
        secure_channel=not settings.scheduler_emulator,
    )
    worker.add_orchestrator(planning_orchestrator)
    worker.add_activity(propose_v1)
    worker.add_activity(fail_v1)
    # Always registered so in-flight suggestion histories can drain; new requests are
    # dispatched only while the run-check assistant is enabled.
    worker.add_orchestrator(run_check_orchestrator)
    worker.add_activity(propose_run_checks_v1)
    worker.add_activity(fail_run_checks_v1)
    try:
        with worker:
            worker.start()  # type: ignore[no-untyped-call]
            while not stop.is_set():
                try:
                    dispatch_once(client)
                    reconcile_once(client)
                except (SQLAlchemyError, grpc.RpcError, AzureError) as exc:
                    logger.error(
                        "Worker reconciliation failed; will retry",
                        extra={
                            "error_type": type(exc).__name__,
                        },
                    )
                if settings.run_assistant_enabled:
                    try:
                        dispatch_run_checks_once(client)
                        reconcile_run_checks_once(client)
                    except (SQLAlchemyError, grpc.RpcError, AzureError) as exc:
                        logger.error(
                            "Run-check reconciliation failed; will retry",
                            extra={"error_type": type(exc).__name__},
                        )
                stop.wait(5)
    finally:
        client.close()
        if credential:
            credential.close()


if __name__ == "__main__":
    main()
