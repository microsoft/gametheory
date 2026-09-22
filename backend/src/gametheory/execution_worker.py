import hashlib
import json
import logging
import signal
import threading
from collections.abc import Generator
from datetime import timedelta
from typing import Any, TypedDict

import grpc
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from durabletask import task
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.azuremanaged.worker import DurableTaskSchedulerWorker
from durabletask.client import OrchestrationStatus
from durabletask.internal.orchestrator_service_pb2 import OrchestrationIdReusePolicy
from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy import or_, select, true, update
from sqlalchemy.exc import SQLAlchemyError

from gametheory.auth import Principal
from gametheory.config import get_settings
from gametheory.execution import RunManifest, compare
from gametheory.execution_adapters import invoke, operation_key
from gametheory.execution_service import (
    check_authorization,
    load_run,
    record_event,
    resolve_operation,
)
from gametheory.logging import configure_logging
from gametheory.persistence import (
    ExerciseRun,
    ExerciseRunState,
    RunDispatch,
    RunStep,
    Workspace,
    new_id,
    now,
    session_factory,
)
from gametheory.preparation import (
    PriorResultReference,
    Scalar,
    validate_expected_version,
    validate_scalar,
)

logger = logging.getLogger(__name__)
VALUES = TypeAdapter(dict[str, Scalar | None])


class Tick(TypedDict):
    done: bool
    delay: float


def tick(delay: float = 0, *, done: bool = False) -> Tick:
    return {"done": done, "delay": max(0, delay)}


def exercise_v1(
    ctx: task.OrchestrationContext, dispatch: str
) -> Generator[task.Task[Any], Any, str]:
    while True:
        try:
            result = yield ctx.call_activity(advance_v1, input=dispatch)
        except task.TaskFailedError:
            yield ctx.call_activity(
                intervene_v1,
                input=dispatch,
                retry_policy=task.RetryPolicy(
                    first_retry_interval=timedelta(seconds=5), max_number_of_attempts=3
                ),
            )
            result = tick(30)
        if result["done"]:
            return dispatch
        if result["delay"] > 0:
            timer = ctx.create_timer(timedelta(seconds=result["delay"]))
            control = ctx.wait_for_external_event("control")
            winner = yield task.when_any([timer, control])
            if winner == timer:
                control.cancel()
            else:
                timer.cancel()


def hold(dispatch_id: str, reason: str) -> Tick:
    with session_factory().begin() as db:
        dispatch = db.get(RunDispatch, dispatch_id)
        run = db.get(ExerciseRun, dispatch.run_id) if dispatch else None
        state = db.get(ExerciseRunState, dispatch.run_id) if dispatch else None
        workspace = db.get(Workspace, run.workspace_id) if run else None
        if workspace and workspace.organization_id != get_settings().tenant_id:
            raise ValueError("Dispatch is outside this executor's tenant")
        if (
            run
            and state
            and dispatch
            and state.phase == dispatch.phase
            and state.active
            and (state.state != "intervention" or state.reason != reason)
        ):
            state.state, state.reason = "intervention", reason
            state.version += 1
            record_event(db, run, "run.intervention", {"reason": reason})
    return tick(30)


exercise_orchestrator: task.Orchestrator[str, str] = exercise_v1


def finish_stop(dispatch_id: str) -> Tick | None:
    # Accounting a requested stop requires no target access or original operator grant.
    with session_factory().begin() as db:
        dispatch = db.get(RunDispatch, dispatch_id)
        run = db.get(ExerciseRun, dispatch.run_id) if dispatch else None
        state = db.get(ExerciseRunState, dispatch.run_id) if dispatch else None
        if not dispatch or not run or not state or not state.stop_requested:
            return None
        workspace = db.get(Workspace, run.workspace_id)
        if workspace is None or workspace.organization_id != get_settings().tenant_id:
            raise ValueError("Dispatch is outside this executor's tenant")
        state = db.scalar(
            select(ExerciseRunState)
            .where(ExerciseRunState.run_id == run.id)
            .with_hint(ExerciseRunState, "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql")
            .execution_options(populate_existing=True)
        )
        if state is None or state.phase != dispatch.phase or not state.active:
            return tick(done=True)
        rows = list(
            db.scalars(
                select(RunStep).where(RunStep.run_id == run.id, RunStep.phase == state.phase)
            )
        )
        if any(
            row.state == "in_flight" and row.lease_until and row.lease_until > now() for row in rows
        ):
            return tick(5)
        for row in rows:
            if row.state == "in_flight":
                row.state, row.reason = "unknown", "No confirmed result after an accepted attempt"
                record_event(
                    db,
                    run,
                    "operation.unknown",
                    {"reason": row.reason, "phase": state.phase, "attempt_id": row.attempt_id},
                    row.step_id,
                )
        incomplete = any(row.state == "unknown" for row in rows)
        state.state = "stopped_incomplete" if incomplete else "stopped"
        state.reason = "Unconfirmed effects remain; stop did not undo them." if incomplete else None
        state.active = False
        state.version += 1
        record_event(db, run, "run.stopped", {"incomplete": incomplete, "phase": state.phase})
        return tick(done=True)


def intervene_v1(_ctx: task.ActivityContext, dispatch_id: str) -> None:
    hold(
        dispatch_id,
        "Execution activity failed. Inspect attempts and reconcile; no automatic mutation retry.",
    )


def advance_v1(_ctx: task.ActivityContext, dispatch_id: str) -> Tick:
    try:
        return advance(dispatch_id)
    except HTTPException as exc:
        return hold(dispatch_id, str(exc.detail))
    except (SQLAlchemyError, ValueError) as exc:
        error_type = type(exc).__name__
    logger.error(
        "Exercise activity failed", extra={"dispatch_id": dispatch_id, "error_type": error_type}
    )
    raise RuntimeError(f"Exercise activity failed ({error_type}); dispatch {dispatch_id}")


def runnable(manifest: RunManifest, rows: dict[str, RunStep]) -> tuple[str | None, list[str]]:
    skipped = []
    controls: dict[str, list[tuple[str, bool]]] = {}
    for step in manifest.preparation.draft.steps:
        if step.condition:
            for branch, desired in (
                (step.condition.if_true, True),
                (step.condition.if_false, False),
            ):
                for target in branch:
                    controls.setdefault(str(target), []).append((str(step.id), desired))
    for step in manifest.preparation.draft.steps:
        sid = str(step.id)
        row = rows[sid]
        if row.state not in {"pending", "waiting"}:
            continue
        dependencies = [rows[str(key)] for key in step.depends_on]
        if any(item.state == "skipped" or item.step_id in skipped for item in dependencies):
            skipped.append(sid)
            continue
        if any(item.state != "succeeded" for item in dependencies):
            continue
        incoming = controls.get(sid, [])
        if incoming:
            activated = any(
                rows[parent].state == "succeeded"
                and json.loads(rows[parent].result).get("condition") is desired
                for parent, desired in incoming
            )
            if not activated:
                if all(
                    rows[parent].state in {"succeeded", "skipped"} or parent in skipped
                    for parent, _ in incoming
                ):
                    skipped.append(sid)
                continue
        return sid, skipped
    return None, skipped


def advance(dispatch_id: str) -> Tick:
    stopped = finish_stop(dispatch_id)
    if stopped is not None:
        return stopped
    factory = session_factory()
    with factory.begin() as db:
        dispatch = db.get(RunDispatch, dispatch_id)
        if dispatch is None:
            raise ValueError("Dispatch missing")
        run = db.get(ExerciseRun, dispatch.run_id)
        if run is None:
            raise ValueError("Run missing")
        actor = Principal(get_settings().tenant_id, run.operator)
        run, state, manifest = load_run(db, actor, run.workspace_id, run.id, mutation=True)
        if dispatch.phase != state.phase or not state.active:
            return tick(done=True)
        rows = {
            row.step_id: row
            for row in db.scalars(
                select(RunStep).where(RunStep.run_id == run.id, RunStep.phase == state.phase)
            )
        }
        for row in rows.values():
            if row.state == "in_flight":
                if row.lease_until and row.lease_until > now():
                    return tick(5)
                row.state, row.reason = "unknown", "Worker lease expired with no confirmed outcome"
                record_event(
                    db,
                    run,
                    "operation.unknown",
                    {"reason": row.reason, "phase": state.phase, "attempt_id": row.attempt_id},
                    row.step_id,
                )
                state.state, state.reason = "intervention", row.reason
                state.version += 1
        if state.stop_requested:
            incomplete = any(row.state == "unknown" for row in rows.values())
            state.state = "stopped_incomplete" if incomplete else "stopped"
            state.reason = (
                "Unconfirmed effects remain; stop did not undo them." if incomplete else None
            )
            state.active = False
            state.version += 1
            record_event(db, run, "run.stopped", {"incomplete": incomplete, "phase": state.phase})
            return tick(done=True)
        if state.state in {"paused", "intervention"}:
            return tick(30)
        if all(
            row.state in {"succeeded", "skipped", "manually_accounted"} for row in rows.values()
        ):
            state.state = (
                "completed"
                if state.phase == "exercise"
                else (
                    "recovered_with_manual_reports"
                    if any(row.state == "manually_accounted" for row in rows.values())
                    else "recovered"
                )
            )
            state.active, state.reason = False, None
            state.version += 1
            record_event(db, run, "run.finished", {"state": state.state, "phase": state.phase})
            return tick(done=True)
        check = check_authorization(db, actor, run, state, manifest)
        if check.blockers:
            raise HTTPException(409, "; ".join(check.blockers))
        window = manifest.preparation.draft.window
        if window and window.starts_at.replace(tzinfo=None) > now():
            state.state = "scheduled"
            return tick((window.starts_at.replace(tzinfo=None) - now()).total_seconds())
        if state.phase == "recovery":
            originals = list(
                db.scalars(
                    select(RunStep)
                    .where(RunStep.run_id == run.id, RunStep.phase == "exercise")
                    .order_by(RunStep.finished_at.desc(), RunStep.step_id.desc())
                )
            )
            candidate = next(
                (
                    rows[item.step_id]
                    for item in originals
                    if item.step_id in rows
                    and rows[item.step_id].state not in {"succeeded", "manually_accounted"}
                ),
                None,
            )
            if candidate is None:
                raise ValueError("Recovery dependency state is inconsistent")
            if candidate.state == "manual_required":
                state.state, state.reason = (
                    "intervention",
                    "Recovery is incomplete. An owned, version-safe external action and an explicit manual report are required.",
                )
                state.version += 1
                return tick(30)
            sid = candidate.step_id
        else:
            chosen, skipped = runnable(manifest, rows)
            for skipped_id in skipped:
                rows[skipped_id].state, rows[skipped_id].finished_at = "skipped", now()
                record_event(db, run, "step.skipped", {"phase": state.phase}, skipped_id)
            if chosen is None:
                if skipped:
                    state.version += 1
                    return tick()
                raise ValueError("No executable successor; inspect branch evidence")
            sid = chosen
        row = rows[sid]
        step = next(item for item in manifest.preparation.draft.steps if str(item.id) == sid)
        current = now()
        if row.started_at is None:
            row.started_at = current
        if state.phase == "exercise" and step.kind == "wait":
            if row.next_at is None:
                row.next_at = current + timedelta(seconds=step.wait_seconds or 0)
            if current < row.next_at:
                row.state, state.state = "waiting", "waiting"
                state.version += 1
                return tick((row.next_at - current).total_seconds())
            row.state, row.finished_at = "succeeded", current
            record_event(db, run, "wait.finished", {"phase": state.phase}, sid)
            state.version += 1
            return tick()
        if state.phase == "exercise" and step.condition:
            condition = step.condition
            source = VALUES.validate_json(rows[str(condition.source_step_id)].result)
            left = source.get(condition.result_field)
            if left is None:
                raise ValueError("Required condition evidence is absent")
            _, source_operation, _ = resolve_operation(
                manifest, str(condition.source_step_id), "exercise"
            )
            result_type = next(
                item.type
                for item in source_operation.results
                if item.name == condition.result_field
            )
            decision = compare(left, condition.operator, condition.value, result_type)
            row.state, row.result, row.finished_at = (
                "succeeded",
                json.dumps({"condition": decision}),
                current,
            )
            record_event(
                db, run, "condition.evaluated", {"condition": decision, "phase": state.phase}, sid
            )
            state.version += 1
            return tick()
        observation = (
            next((item for item in manifest.observations if str(item.step_id) == sid), None)
            if state.phase == "exercise"
            else None
        )
        if (
            observation
            and row.samples
            and (
                row.samples >= observation.max_samples
                or current >= row.started_at + timedelta(seconds=observation.timeout_seconds)
            )
        ):
            row.state, row.finished_at = "succeeded", current
            row.reason = "Observation budget ended without a matching sample; missing coverage is not participant failure."
            record_event(
                db, run, "observation.ended", {"phase": state.phase, "reason": row.reason}, sid
            )
            state.version += 1
            return tick()
        if row.next_at and row.next_at > current:
            return tick((row.next_at - current).total_seconds())
        total = list(db.scalars(select(RunStep.samples).where(RunStep.run_id == run.id)))
        if sum(total) >= manifest.max_operations:
            raise HTTPException(409, "The pinned operation budget has been exhausted")
        binding, operation, declared = resolve_operation(manifest, sid, state.phase)
        parameters: dict[str, Scalar] = {}
        if row.parameters is not None:
            parameters = TypeAdapter(dict[str, Scalar]).validate_json(row.parameters)
        else:
            for name, value in declared.items():
                if name == "idempotency_key" and operation.invocation.kind == "sql":
                    continue
                if isinstance(value, PriorResultReference):
                    source_row = db.get(RunStep, (run.id, "exercise", str(value.source_step_id)))
                    if source_row is None or source_row.state != "succeeded":
                        raise ValueError("Required predecessor is not successful")
                    source = VALUES.validate_json(source_row.result)
                    value = source.get(value.field)
                if value is not None:
                    parameters[name] = value
            row.parameters = json.dumps(parameters, allow_nan=False, sort_keys=True)
        for operation_field in operation.parameters:
            if operation_field.name == "idempotency_key" and operation.invocation.kind == "sql":
                continue
            value = parameters.get(operation_field.name)
            if value is None and operation_field.required:
                raise ValueError("Required operation result is missing")
            if value is not None:
                validate_scalar(operation_field, value)
                if (
                    operation.invocation.kind == "rest"
                    and operation_field.name == "expected_version"
                ):
                    validate_expected_version(value)
        target = check.targets[binding.configuration_id]
        attempt_id = new_id()
        key = operation_key(run.id, state.phase, sid)
        row.attempt_id, row.state = attempt_id, "in_flight"
        row.lease_until = current + timedelta(seconds=target.timeout_seconds * 3 + 60)
        row.samples += 1
        state.state, state.reason = "running", None
        state.version += 1
        record_event(
            db,
            run,
            "attempt.started",
            {
                "attempt_id": attempt_id,
                "key": key,
                "phase": state.phase,
                "payload_digest": hashlib.sha256(row.parameters.encode()).hexdigest(),
                "identity_ref": target.identity_ref,
                "binding_digest": target.digest,
            },
            sid,
        )
        rid, wid, phase = run.id, run.workspace_id, state.phase
    # Recheck after the durable attempt commit and immediately before external I/O.
    with factory.begin() as db:
        run, state, manifest = load_run(db, actor, wid, rid, mutation=True)
        attempt_row = db.get(RunStep, (rid, phase, sid))
        if attempt_row is None or attempt_row.attempt_id != attempt_id:
            raise ValueError("Attempt fence changed")
        row = attempt_row
        check = check_authorization(db, actor, run, state, manifest)
        if (
            state.phase != phase
            or state.state == "paused"
            or state.stop_requested
            or check.blockers
        ):
            row.state, row.lease_until = "pending", None
            record_event(
                db, run, "attempt.not_dispatched", {"attempt_id": attempt_id, "phase": phase}, sid
            )
            if check.blockers and not state.stop_requested and state.state != "paused":
                state.state, state.reason = "intervention", "; ".join(check.blockers)
            state.version += 1
            return tick(5)
    result = invoke(target, operation, parameters, key)
    with factory.begin() as db:
        run = db.get(ExerciseRun, rid)
        saved_state = db.scalar(
            select(ExerciseRunState)
            .where(ExerciseRunState.run_id == rid)
            .with_hint(ExerciseRunState, "WITH (UPDLOCK, HOLDLOCK)", dialect_name="mssql")
        )
        saved_step = db.get(RunStep, (rid, phase, sid))
        if run is None or saved_state is None or saved_step is None:
            raise ValueError("Effect accounting records are unavailable")
        state, row = saved_state, saved_step
        record_event(
            db,
            run,
            f"operation.{result.outcome}",
            {
                "attempt_id": attempt_id,
                "phase": phase,
                "result": json.dumps(result.values, allow_nan=False),
                "reason": result.reason,
            },
            sid,
        )
        if row.attempt_id != attempt_id:
            return tick()
        row.result, row.reason, row.lease_until = (
            json.dumps(result.values, allow_nan=False),
            result.reason,
            None,
        )
        row.state, row.finished_at = result.outcome, now()
        if result.outcome == "succeeded" and observation:
            value = result.values.get(observation.field)
            result_type = next(
                item.type for item in operation.results if item.name == observation.field
            )
            matched = value is not None and compare(
                value, observation.operator, observation.value, result_type
            )
            record_event(
                db,
                run,
                "observation",
                {
                    "phase": phase,
                    "result": json.dumps(result.values, allow_nan=False),
                    "matched": matched,
                    "attempt_id": attempt_id,
                },
                sid,
            )
            if not matched:
                row.state, row.finished_at = "waiting", None
                row.next_at = now() + timedelta(seconds=observation.interval_seconds)
        if result.outcome != "succeeded" and not state.stop_requested:
            state.state, state.reason = (
                "intervention",
                result.reason or f"Operation {result.outcome}",
            )
        state.version += 1
    return tick()


def dispatch_once(client: DurableTaskSchedulerClient) -> None:
    factory = session_factory()
    with factory() as db:
        ids = list(
            db.scalars(
                select(RunDispatch.id)
                .join(ExerciseRunState, ExerciseRunState.run_id == RunDispatch.run_id)
                .join(ExerciseRun, ExerciseRun.id == RunDispatch.run_id)
                .join(Workspace, Workspace.id == ExerciseRun.workspace_id)
                .where(
                    Workspace.organization_id == get_settings().tenant_id,
                    ExerciseRunState.active == true(),
                    RunDispatch.phase == ExerciseRunState.phase,
                    RunDispatch.next_at <= now(),
                    or_(
                        RunDispatch.state == "scheduled",
                        (RunDispatch.next_at <= now())
                        & or_(
                            RunDispatch.state == "pending",
                            (RunDispatch.state == "leased") & (RunDispatch.lease_until < now()),
                        ),
                    ),
                )
                .order_by(RunDispatch.next_at)
                .limit(100)
            )
        )
    for did in ids:
        with factory.begin() as db:
            dispatch = db.get(RunDispatch, did)
            if dispatch is None:
                continue
            state = db.get(ExerciseRunState, dispatch.run_id)
            if state is None or state.phase != dispatch.phase or not state.active:
                continue
            control_version, delivered = dispatch.control_version, dispatch.delivered_version
            scheduled = dispatch.state == "scheduled"
            if scheduled:
                dispatch.next_at = now() + timedelta(seconds=5)
            token = new_id()
            if not scheduled:
                claimed = db.execute(
                    update(RunDispatch)
                    .where(
                        RunDispatch.id == did,
                        or_(
                            RunDispatch.state == "pending",
                            (RunDispatch.state == "leased") & (RunDispatch.lease_until < now()),
                        ),
                    )
                    .values(
                        state="leased",
                        lease_token=token,
                        lease_until=now() + timedelta(minutes=2),
                        attempts=RunDispatch.attempts + 1,
                    )
                    .returning(RunDispatch.id)
                ).scalar_one_or_none()
                if claimed is None:
                    continue
        try:
            instance = f"exercise-{did}"
            remote = client.get_orchestration_state(instance, fetch_payloads=False)
            if remote is None and scheduled:
                hold(did, "Scheduler history is missing; do not redispatch an uncertain run.")
                continue
            if remote is None:
                client.schedule_new_orchestration(
                    exercise_orchestrator,
                    instance_id=instance,
                    input=did,
                    reuse_id_policy=OrchestrationIdReusePolicy(replaceableStatus=[]),
                )
            elif remote.runtime_status in {
                OrchestrationStatus.FAILED,
                OrchestrationStatus.TERMINATED,
                OrchestrationStatus.COMPLETED,
            }:
                hold(did, "Workflow ended while the run remains active; inspect and reconcile.")
                continue
            if control_version != delivered:
                client.raise_orchestration_event(instance, "control", data=control_version)
            with factory.begin() as db:
                query = update(RunDispatch).where(RunDispatch.id == did)
                if not scheduled:
                    query = query.where(RunDispatch.lease_token == token)
                db.execute(
                    query.values(
                        state="scheduled",
                        delivered_version=control_version,
                        next_at=now() + timedelta(seconds=5),
                    )
                )
        except (grpc.RpcError, AzureError) as exc:
            logger.error(
                "Exercise dispatch unavailable",
                extra={"dispatch_id": did, "error_type": type(exc).__name__},
            )
            with factory.begin() as db:
                dispatch = db.get(RunDispatch, did)
                if dispatch and not scheduled and dispatch.lease_token == token:
                    dispatch.state = "pending"
                    dispatch.next_at = now() + timedelta(
                        seconds=min(300, 2 ** min(dispatch.attempts, 8))
                    )


def main() -> None:
    settings = get_settings()
    if not settings.execution_enabled:
        raise SystemExit("GT_EXECUTION_ENABLED and approved target/runtime settings are required")
    configure_logging()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    credential = (
        None
        if settings.scheduler_emulator
        else DefaultAzureCredential(authority=settings.profile.authority)
    )
    client = DurableTaskSchedulerClient(
        host_address=settings.scheduler_endpoint,
        taskhub=settings.execution_taskhub,
        token_credential=credential,
        secure_channel=not settings.scheduler_emulator,
    )
    worker = DurableTaskSchedulerWorker(
        host_address=settings.scheduler_endpoint,
        taskhub=settings.execution_taskhub,
        token_credential=credential,
        secure_channel=not settings.scheduler_emulator,
    )
    worker.add_orchestrator(exercise_orchestrator)
    worker.add_activity(advance_v1)
    worker.add_activity(intervene_v1)
    try:
        with worker:
            worker.start()  # type: ignore[no-untyped-call]
            while not stop.is_set():
                try:
                    dispatch_once(client)
                except (SQLAlchemyError, grpc.RpcError, AzureError) as exc:
                    logger.error(
                        "Exercise reconciliation unavailable",
                        extra={"error_type": type(exc).__name__},
                    )
                stop.wait(5)
    finally:
        client.close()
        if credential:
            credential.close()


if __name__ == "__main__":
    main()
