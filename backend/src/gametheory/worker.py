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

from gametheory.auth import Principal, authorize
from gametheory.config import get_settings
from gametheory.domain import ProposalContent, ScenarioContent
from gametheory.logging import configure_logging
from gametheory.persistence import (
    DispatchIntent,
    PlanningRequest,
    Scenario,
    Workspace,
    new_id,
    now,
    session_factory,
)
from gametheory.planning import generate_proposal
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
                stop.wait(5)
    finally:
        client.close()
        if credential:
            credential.close()


if __name__ == "__main__":
    main()
