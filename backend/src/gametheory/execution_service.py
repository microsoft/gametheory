import json
from dataclasses import dataclass, field
from datetime import UTC, timedelta
from uuid import UUID, uuid4, uuid5

from fastapi import HTTPException
from pydantic import TypeAdapter
from sqlalchemy import exists, select, true, update
from sqlalchemy.orm import Session

from gametheory.auth import Principal, authorize, is_admin, require_admin
from gametheory.config import get_settings
from gametheory.environment_policy import latest_policy, policy_view
from gametheory.execution import (
    BlockerRemedy,
    Capability,
    ExecutionGrantInput,
    ExecutionGrantView,
    ManualRecoveryInput,
    PreflightRecovery,
    PreflightTarget,
    ReadinessView,
    RunApprovalInput,
    RunAuthorizationView,
    RunBlocker,
    RunControl,
    RunCreate,
    RunEventView,
    RunManifest,
    RunPreflightView,
    RunStepView,
    RunView,
    TargetAuthorityView,
    execution_issues,
    planned_attempts,
)
from gametheory.execution_adapters import TargetBinding, binding_for, bindings
from gametheory.persistence import (
    BoardContributor,
    BoardOrigin,
    Environment,
    EnvironmentPolicyRecord,
    ExecutionGrant,
    ExecutionGrantRevocation,
    ExerciseRun,
    ExerciseRunState,
    PreparationBoard,
    PreparationPreview,
    RunApproval,
    RunApprovalRevocation,
    RunAuthorization,
    RunDispatch,
    RunEvent,
    RunStep,
    TargetReadiness,
    new_id,
    now,
    timestamp,
)
from gametheory.preparation import (
    ConfigurationSnapshot,
    FieldName,
    OperationBinding,
    OperationDefinition,
    PreparationManifest,
    PriorResultReference,
    Scalar,
    canonical_digest,
    validate_bindings,
)
from gametheory.preparation_service import (
    board_record,
    configuration_record,
    pin_asset,
    preparation_workspace,
    require_preview_access,
    withdrawal,
)
from gametheory.service import audit


def grant_record(db: Session, wid: str, oid: str, capability: str) -> ExecutionGrant | None:
    revoked = exists(
        select(ExecutionGrantRevocation.grant_id)
        .where(ExecutionGrantRevocation.grant_id == ExecutionGrant.id)
        .with_hint(ExecutionGrantRevocation, "WITH (HOLDLOCK)", dialect_name="mssql")
    )
    return db.scalar(
        select(ExecutionGrant)
        .where(
            ExecutionGrant.workspace_id == wid,
            ExecutionGrant.object_id == oid,
            ExecutionGrant.capability == capability,
            ~revoked,
        )
        .with_hint(ExecutionGrant, "WITH (HOLDLOCK)", dialect_name="mssql")
        .execution_options(populate_existing=True)
    )


def grant_view(record: ExecutionGrant) -> ExecutionGrantView:
    return ExecutionGrantView.model_validate(
        {
            "id": record.id,
            "workspace_id": record.workspace_id,
            "object_id": record.object_id,
            "capability": record.capability,
            "granted_by": record.granted_by,
            "granted_at": timestamp(record.created_at),
        }
    )


def list_grants(db: Session, actor: Principal, wid: str) -> list[ExecutionGrantView]:
    authorize(db, actor, wid)
    revoked = exists(
        select(ExecutionGrantRevocation.grant_id).where(
            ExecutionGrantRevocation.grant_id == ExecutionGrant.id
        )
    )
    return [
        grant_view(record)
        for record in db.scalars(
            select(ExecutionGrant)
            .where(ExecutionGrant.workspace_id == wid, ~revoked)
            .order_by(ExecutionGrant.created_at)
        )
    ]


def set_grant(
    db: Session, actor: Principal, wid: str, body: ExecutionGrantInput, correlation: str
) -> ExecutionGrantView:
    preparation_workspace(db, actor, wid, mutation=True)
    require_admin(db, actor, fence=True)
    authorize(db, Principal(actor.tenant, str(body.object_id)), wid, fence=True)
    record = grant_record(db, wid, str(body.object_id), body.capability)
    if record is None:
        record = ExecutionGrant(
            workspace_id=wid,
            object_id=str(body.object_id),
            capability=body.capability,
            granted_by=actor.object_id,
        )
        db.add(record)
        db.flush()
        audit(db, actor, "execution.grant", record.id, wid, correlation=correlation)
    return grant_view(record)


def revoke_grant(
    db: Session, actor: Principal, wid: str, oid: str, capability: Capability, correlation: str
) -> None:
    preparation_workspace(db, actor, wid, mutation=True)
    require_admin(db, actor, fence=True)
    record = grant_record(db, wid, oid, capability)
    if record:
        db.add(ExecutionGrantRevocation(grant_id=record.id, actor=actor.object_id))
        audit(db, actor, "execution.grant_revoked", record.id, wid, correlation=correlation)
        wake_workspace(db, wid)


def wake_workspace(db: Session, wid: str) -> None:
    db.execute(
        update(RunDispatch)
        .where(
            RunDispatch.run_id.in_(select(ExerciseRun.id).where(ExerciseRun.workspace_id == wid))
        )
        .values(control_version=RunDispatch.control_version + 1)
    )


def revoke_execution_access(db: Session, actor: Principal, wid: str, oid: str) -> None:
    for capability in ("operator", "reviewer"):
        grant = grant_record(db, wid, oid, capability)
        if grant:
            db.add(ExecutionGrantRevocation(grant_id=grant.id, actor=actor.object_id))
            audit(db, actor, "execution.membership_revoked", grant.id, wid)
    wake_workspace(db, wid)


def record_event(
    db: Session,
    run: ExerciseRun,
    kind: str,
    detail: dict[str, Scalar | None],
    step_id: str | None = None,
) -> RunEvent:
    event = RunEvent(
        run_id=run.id, kind=kind, step_id=step_id, detail=json.dumps(detail, allow_nan=False)
    )
    db.add(event)
    return event


def load_run(
    db: Session,
    actor: Principal,
    wid: str,
    rid: str,
    *,
    mutation: bool = False,
    version: int | None = None,
) -> tuple[ExerciseRun, ExerciseRunState, RunManifest]:
    preparation_workspace(db, actor, wid, mutation=mutation)
    run = db.get(ExerciseRun, rid)
    if run is None or run.workspace_id != wid:
        raise HTTPException(404, "Run not found")
    state = db.scalar(
        select(ExerciseRunState)
        .where(ExerciseRunState.run_id == rid)
        .with_hint(
            ExerciseRunState,
            "WITH (UPDLOCK, HOLDLOCK)" if mutation else "WITH (HOLDLOCK)",
            dialect_name="mssql",
        )
        .execution_options(populate_existing=True)
    )
    if state is None:
        raise HTTPException(409, "Run state is unavailable")
    if version is not None and version != state.version:
        raise HTTPException(409, "Run changed. Reload before issuing another action.")
    manifest = RunManifest.model_validate_json(run.manifest)
    if (
        manifest.digest != run.digest
        or str(manifest.run_id) != rid
        or str(manifest.preparation.workspace_id) != wid
        or str(manifest.preparation.board_id) != run.board_id
    ):
        raise HTTPException(409, "Run manifest integrity check failed")
    for config in manifest.preparation.configurations:
        configuration_record(db, actor, wid, str(config.id))
    return run, state, manifest


@dataclass(frozen=True)
class PreflightRun:
    """An unsaved run subject. Preflight evaluates authority without persisting a run."""

    id: str
    workspace_id: str
    board_id: str
    operator: str
    grant_id: str


@dataclass(frozen=True)
class PreflightState:
    phase: str = "exercise"
    context_id: str | None = None


RunSubject = ExerciseRun | PreflightRun
RunPhase = ExerciseRunState | PreflightState
TARGET_BLOCKERS: dict[str, tuple[str, BlockerRemedy]] = {
    "configuration": ("configuration_changed", "preparation"),
    "environment": ("environment_unavailable", "environment_policy"),
    "policy": ("environment_execution_disabled", "environment_policy"),
    "classification": ("classification_conflict", "environment_policy"),
    "binding": ("target_binding_rejected", "target_bindings"),
    "operation": ("operation_not_bound", "target_bindings"),
    "readiness": ("readiness_missing", "readiness"),
}


def reviewer_allowed(db: Session, actor: Principal, run: RunSubject) -> bool:
    if (
        actor.object_id == run.operator
        or grant_record(db, run.workspace_id, actor.object_id, "reviewer") is None
    ):
        return False
    origin = db.get(BoardOrigin, run.board_id)
    return bool(
        origin
        and origin.created_by != actor.object_id
        and db.get(BoardContributor, (run.board_id, actor.object_id)) is None
    )


@dataclass
class Authorization:
    required: bool = False
    policy_resolved: bool = True
    policies: dict[str, int] = field(default_factory=dict)
    targets: dict[UUID, TargetBinding] = field(default_factory=dict)
    readiness: list[str] = field(default_factory=list)
    receipts: dict[UUID, str] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    details: list[RunBlocker] = field(default_factory=list)
    approval_status: str = "not_required"
    approval: RunApproval | None = None

    @property
    def binding_digest(self) -> str:
        return canonical_digest({str(cid): binding.digest for cid, binding in self.targets.items()})

    def block(
        self,
        code: str,
        message: str,
        remedy: BlockerRemedy,
        *,
        environment_id: UUID | None = None,
        configuration_id: UUID | None = None,
    ) -> None:
        self.blockers.append(message)
        self.details.append(
            RunBlocker(
                code=code,
                message=message,
                remedy=remedy,
                environment_id=environment_id,
                configuration_id=configuration_id,
            )
        )


def check_authorization(
    db: Session,
    actor: Principal,
    run: RunSubject,
    state: RunPhase,
    manifest: RunManifest,
    *,
    require_context: bool = True,
    require_approval: bool = True,
) -> Authorization:
    result = Authorization()
    operator = Principal(actor.tenant, run.operator)
    try:
        authorize(db, operator, run.workspace_id, fence=True)
    except HTTPException:
        result.block(
            "operator_access_lost",
            "Run operator no longer has workspace access",
            "workspace_access",
        )
    grant = grant_record(db, run.workspace_id, run.operator, "operator")
    if grant is None or grant.id != run.grant_id:
        result.block(
            "operator_grant_inactive", "Original operator grant is no longer active", "run_access"
        )
    settings = get_settings()
    if not settings.execution_enabled:
        result.block("runtime_disabled", "Exercise runtime is disabled", "runtime")
    available: dict[UUID, TargetBinding] = {}
    try:
        available = bindings()
    except HTTPException as exc:
        result.block("bindings_unavailable", str(exc.detail), "target_bindings")
    window = manifest.preparation.draft.window
    current = now()
    if window is None or window.ends_at.replace(tzinfo=None) <= current:
        result.block("window_ended", "Execution window has ended", "preparation")
    context = db.get(RunAuthorization, state.context_id) if state.context_id else None
    pinned_readiness = json.loads(context.readiness_ids) if context and require_context else None
    for config in manifest.preparation.configurations:
        result.required |= config.content.classification == "production"
        cid = str(config.id)
        stage = "configuration"
        try:
            current_config = configuration_record(db, operator, run.workspace_id, cid)
            if ConfigurationSnapshot.model_validate_json(
                current_config.snapshot
            ) != config or withdrawal(db, cid):
                raise HTTPException(409, "Configuration changed or was withdrawn")
            stage = "environment"
            environment = db.get(Environment, str(config.environment_id))
            if environment is None or environment.organization_id != actor.tenant:
                raise HTTPException(409, "Environment is unavailable")
            policy = latest_policy(db, environment.id)
            if policy.classification == "unknown":
                result.policy_resolved = False
            result.policies[environment.id] = policy.version
            result.required |= (
                policy.approval_required
                or policy.classification == "production"
                or config.content.classification == "production"
            )
            stage = "policy"
            if policy.classification == "unknown" or not policy.execution_enabled:
                raise HTTPException(409, f"Execution is not enabled for {environment.name}")
            stage = "classification"
            if policy.classification != config.content.classification:
                raise HTTPException(409, "Environment and target classifications conflict")
            stage = "binding"
            target = binding_for(config, available)
            result.targets[config.id] = target
            stage = "operation"
            for step in manifest.preparation.draft.steps:
                if step.binding and step.binding.configuration_id == config.id:
                    operation = next(
                        item
                        for item in config.content.catalog.operations
                        if (item.key, item.version)
                        == (step.binding.operation_key, step.binding.operation_version)
                    )
                    target.operation(operation)
            for recovery in manifest.recovery:
                if recovery.binding.configuration_id == config.id:
                    operation = next(
                        item
                        for item in config.content.catalog.operations
                        if (item.key, item.version)
                        == (recovery.binding.operation_key, recovery.binding.operation_version)
                    )
                    target.operation(operation)
            stage = "readiness"
            readiness_query = select(TargetReadiness).where(
                TargetReadiness.configuration_id == cid,
                TargetReadiness.configuration_digest == config.digest,
                TargetReadiness.binding_digest == target.digest,
                TargetReadiness.checked_at <= current,
                TargetReadiness.expires_at > current,
            )
            if window:
                readiness_query = readiness_query.where(
                    TargetReadiness.expires_at >= window.ends_at.replace(tzinfo=None)
                )
            if pinned_readiness is not None:
                readiness_query = readiness_query.where(TargetReadiness.id.in_(pinned_readiness))
            evidence = db.scalar(
                readiness_query.order_by(TargetReadiness.checked_at.desc()).limit(1)
            )
            if evidence is None:
                raise HTTPException(
                    409, "No current operator readiness receipt covering this target and window"
                )
            result.readiness.append(evidence.id)
            result.receipts[config.id] = evidence.id
        except HTTPException as exc:
            code, remedy = TARGET_BLOCKERS[stage]
            result.block(
                code,
                f"{config.connection_name}: {exc.detail}",
                remedy,
                environment_id=config.environment_id,
                configuration_id=config.id,
            )
    for asset in manifest.preparation.assets:
        try:
            if pin_asset(db, run.workspace_id, str(asset.id)) != asset:
                raise HTTPException(409, "Asset integrity changed")
        except HTTPException:
            result.block("asset_unavailable", "A pinned asset is no longer available", "assets")
    context_matches = False
    if context:
        try:
            pinned_targets = stored_bindings(context)
            context_matches = (
                context.run_id == run.id
                and context.phase == state.phase
                and json.loads(context.policy_versions) == result.policies
                and context.binding_digest == result.binding_digest
                and canonical_digest(
                    {str(item.configuration_id): item.digest for item in pinned_targets}
                )
                == context.binding_digest
                and len({item.configuration_id for item in pinned_targets}) == len(pinned_targets)
                and sorted(json.loads(context.readiness_ids)) == sorted(result.readiness)
                and context.approval_required == result.required
            )
        except HTTPException as exc:
            result.block("authorization_invalid", str(exc.detail), "authorize")
    if require_context:
        if context is None:
            result.block(
                "authorization_required",
                "Authorize this exact run under the current environment policies",
                "authorize",
            )
        elif not context_matches:
            result.block(
                "authorization_changed",
                "Authorization context changed; explicit reauthorization is required",
                "authorize",
            )
    result.policy_resolved &= set(result.policies) == {
        str(config.environment_id) for config in manifest.preparation.configurations
    }
    result.approval_status = "required" if result.required else "not_required"
    if result.required and context and require_context:
        approval = db.scalar(
            select(RunApproval)
            .where(RunApproval.context_id == context.id)
            .order_by(RunApproval.sequence.desc())
            .limit(1)
        )
        result.approval = approval
        if approval:
            result.approval_status = "invalid"
            reviewer = Principal(actor.tenant, approval.reviewer)
            reviewer_grant = grant_record(db, run.workspace_id, approval.reviewer, "reviewer")
            try:
                authorize(db, reviewer, run.workspace_id, fence=True)
                reviewer_valid = reviewer_allowed(db, reviewer, run)
            except HTTPException:
                reviewer_valid = False
            if approval.decision == "rejected":
                result.approval_status = "rejected"
            elif (
                reviewer_valid
                and context_matches
                and reviewer_grant
                and reviewer_grant.id == approval.grant_id
                and approval.expires_at > current
                and window
                and approval.expires_at >= window.ends_at.replace(tzinfo=None)
                and db.get(RunApprovalRevocation, approval.id) is None
            ):
                result.approval_status = "approved"
    if require_approval and result.required and result.approval_status != "approved":
        result.block(
            "approval_required",
            "A current independent execution approval is required",
            "approval",
        )
    if not result.policy_resolved:
        result.approval_status = "unresolved"
        result.block(
            "policy_unresolved",
            "Environment approval policy could not be resolved",
            "environment_policy",
        )
    return result


def stored_bindings(context: RunAuthorization) -> list[TargetBinding]:
    try:
        return TypeAdapter(list[TargetBinding]).validate_json(context.target_bindings)
    except ValueError as exc:
        raise HTTPException(409, "The immutable execution target snapshot is invalid") from exc


def require_operator(
    db: Session, actor: Principal, run: ExerciseRun, *, original: bool = True
) -> None:
    grant = grant_record(db, run.workspace_id, actor.object_id, "operator")
    if grant is None:
        raise HTTPException(403, "An explicit execution operator grant is required")
    if original and (actor.object_id != run.operator or grant.id != run.grant_id):
        raise HTTPException(403, "The original execution operator grant is no longer active")


def run_view(
    db: Session, actor: Principal, run: ExerciseRun, state: ExerciseRunState, manifest: RunManifest
) -> RunView:
    from gametheory.execution_evidence import findings

    check = check_authorization(db, actor, run, state, manifest)
    authority = None
    if state.context_id:
        try:
            authority = authorization_view(db, run, state.context_id, manifest)
        except HTTPException as exc:
            check.block("authorization_unavailable", str(exc.detail), "authorize")
    details = (
        [RunBlocker(code="run_state", message=state.reason, remedy="run_state")]
        if state.reason
        else []
    ) + check.details
    # Keep the same order as the string list, preferring the most specific code per message.
    unique_details = list({item.message: item for item in details}.values())
    events = list(
        db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run.id)
            .order_by(RunEvent.created_at, RunEvent.id)
        )
    )
    steps = list(
        db.scalars(
            select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.phase, RunStep.step_id)
        )
    )
    operator_grant = grant_record(db, run.workspace_id, actor.object_id, "operator")
    return RunView.model_validate(
        {
            "id": run.id,
            "board_id": run.board_id,
            "version": state.version,
            "state": state.state,
            "phase": state.phase,
            "operator": run.operator,
            "created_at": timestamp(run.created_at),
            "manifest_digest": run.digest,
            "manifest": manifest,
            "context_id": state.context_id,
            "authorization": authority,
            "approval_required": check.required if check.policy_resolved else None,
            "approval_status": check.approval_status,
            "blockers": list(
                dict.fromkeys(([state.reason] if state.reason else []) + check.blockers)
            ),
            "blocker_details": unique_details,
            "can_operate": actor.object_id == run.operator
            and operator_grant is not None
            and operator_grant.id == run.grant_id,
            "can_stop": operator_grant is not None,
            "can_review": reviewer_allowed(db, actor, run),
            "steps": [
                RunStepView.model_validate(
                    {
                        "step_id": step.step_id,
                        "phase": step.phase,
                        "state": step.state,
                        "result": json.loads(step.result),
                        "reason": step.reason,
                        "samples": step.samples,
                        "started_at": timestamp(step.started_at) if step.started_at else None,
                        "finished_at": timestamp(step.finished_at) if step.finished_at else None,
                    }
                )
                for step in steps
            ],
            "events": [
                RunEventView.model_validate(
                    {
                        "id": event.id,
                        "kind": event.kind,
                        "step_id": event.step_id,
                        "created_at": timestamp(event.created_at),
                        "detail": json.loads(event.detail),
                    }
                )
                for event in events
            ],
            "findings": findings(manifest, events),
        }
    )


def authorization_view(
    db: Session,
    run: ExerciseRun,
    context_id: str,
    manifest: RunManifest,
) -> RunAuthorizationView:
    context = db.get(RunAuthorization, context_id)
    if context is None or context.run_id != run.id:
        raise HTTPException(409, "Execution authorization details are unavailable")
    targets = stored_bindings(context)
    configs = {str(item.id): item for item in manifest.preparation.configurations}
    if {str(item.configuration_id) for item in targets} != set(configs) or canonical_digest(
        {str(item.configuration_id): item.digest for item in targets}
    ) != context.binding_digest:
        raise HTTPException(409, "Execution target snapshot integrity check failed")
    policies = []
    environments = {str(item.environment_id) for item in manifest.preparation.configurations}
    versions = json.loads(context.policy_versions)
    if set(versions) != environments:
        raise HTTPException(409, "Execution policy snapshot is unavailable")
    for eid, version in versions.items():
        environment = db.get(Environment, eid)
        record = db.get(EnvironmentPolicyRecord, (eid, version))
        if environment is None or record is None:
            raise HTTPException(409, "Execution policy history is unavailable")
        policies.append(policy_view(environment, record))
    receipts = []
    for receipt_id in json.loads(context.readiness_ids):
        receipt = db.get(TargetReadiness, receipt_id)
        if receipt is None or receipt.configuration_id not in configs:
            raise HTTPException(409, "Execution readiness evidence is unavailable")
        receipts.append(readiness_view(receipt))
    return RunAuthorizationView(
        id=UUID(context.id),
        created_by=UUID(context.actor),
        created_at=timestamp(context.created_at),
        policies=policies,
        readiness=receipts,
        targets=[target_authority(target) for target in targets],
    )


def readiness_view(receipt: TargetReadiness) -> ReadinessView:
    return ReadinessView(
        id=UUID(receipt.id),
        configuration_id=UUID(receipt.configuration_id),
        checked_at=timestamp(receipt.checked_at),
        expires_at=timestamp(receipt.expires_at),
        evidence_reference=receipt.evidence_reference,
        operator=receipt.actor,
    )


def target_authority(target: TargetBinding) -> TargetAuthorityView:
    return TargetAuthorityView(
        configuration_id=target.configuration_id,
        resource_id=target.resource_id,
        endpoint=target.endpoint,
        database=target.database,
        identity_ref=target.identity_ref,
        client_id=target.client_id,
        token_scope=target.token_scope,
        operation_digests=[item.digest for item in target.operations],
        replayable_operations=[item.digest for item in target.operations if item.safe_replay],
    )


def pinned_preparation(
    db: Session, actor: Principal, wid: str, board: PreparationBoard, body: RunCreate
) -> PreparationManifest:
    preview = db.get(PreparationPreview, str(body.preview_id))
    if (
        preview is None
        or preview.board_id != board.id
        or preview.board_version != board.version
        or preview.digest != body.preview_digest
    ):
        raise HTTPException(409, "Freeze and select the exact current preparation preview")
    require_preview_access(db, actor, wid, preview)
    if canonical_digest(json.loads(preview.manifest)) != preview.digest:
        raise HTTPException(409, "Preview integrity check failed")
    return PreparationManifest.model_validate_json(preview.manifest)


def create_run(
    db: Session,
    actor: Principal,
    wid: str,
    bid: str,
    body: RunCreate,
    version: int,
    correlation: str,
) -> RunView:
    board = board_record(db, actor, wid, bid, mutation=True, version=version)
    grant = grant_record(db, wid, actor.object_id, "operator")
    if grant is None:
        raise HTTPException(403, "An explicit execution operator grant is required")
    preparation = pinned_preparation(db, actor, wid, board, body)
    rid = new_id()
    try:
        manifest = RunManifest(
            run_id=UUID(rid),
            preparation=preparation,
            trigger=body.trigger,
            observations=body.observations,
            objectives=body.objectives,
            recovery=body.recovery,
        )
    except ValueError as exc:
        raise HTTPException(422, "Preparation is not executable: " + str(exc)) from exc
    run = ExerciseRun(
        id=rid,
        board_id=bid,
        workspace_id=wid,
        operator=actor.object_id,
        grant_id=grant.id,
        manifest=manifest.model_dump_json(),
        digest=manifest.digest,
    )
    db.add(run)
    db.flush()
    state = ExerciseRunState(run_id=rid, board_id=bid)
    db.add(state)
    for step in manifest.preparation.draft.steps:
        db.add(RunStep(run_id=rid, phase="exercise", step_id=str(step.id)))
    record_event(db, run, "run.prepared", {"actor": actor.object_id, "manifest_digest": run.digest})
    audit(db, actor, "execution.prepared", rid, wid, correlation=correlation)
    db.flush()
    return run_view(db, actor, run, state, manifest)


def preflight_run(
    db: Session, actor: Principal, wid: str, bid: str, body: RunCreate, version: int
) -> RunPreflightView:
    """Evaluate a proposed run exactly as creation and authorization would, persisting nothing."""

    board = board_record(db, actor, wid, bid, version=version)
    grant = grant_record(db, wid, actor.object_id, "operator")
    if grant is None:
        raise HTTPException(403, "An explicit execution operator grant is required")
    preparation = pinned_preparation(db, actor, wid, board, body)
    issues = list(execution_issues(preparation, body.observations, body.objectives, body.recovery))
    flagged = {(issue.section, issue.index) for issue in issues if issue.index is not None}
    usable_recovery = [
        item for index, item in enumerate(body.recovery) if ("recovery", index) not in flagged
    ]
    # Authority is evaluated for the usable subset; this unvalidated copy is never stored.
    manifest = RunManifest.model_construct(
        run_id=uuid4(),
        preparation=preparation,
        trigger=body.trigger,
        observations=[
            item
            for index, item in enumerate(body.observations)
            if ("observations", index) not in flagged
        ],
        objectives=[
            item
            for index, item in enumerate(body.objectives)
            if ("objectives", index) not in flagged
        ],
        recovery=usable_recovery,
    )
    check = check_authorization(
        db,
        actor,
        PreflightRun(
            id=str(manifest.run_id),
            workspace_id=wid,
            board_id=bid,
            operator=actor.object_id,
            grant_id=grant.id,
        ),
        PreflightState(),
        manifest,
        require_context=False,
        require_approval=False,
    )
    environments = []
    for eid in dict.fromkeys(str(config.environment_id) for config in preparation.configurations):
        environment = db.get(Environment, eid)
        if environment is None or environment.organization_id != actor.tenant:
            continue
        try:
            environments.append(policy_view(environment, latest_policy(db, eid)))
        except HTTPException:
            continue
    targets = []
    for config in preparation.configurations:
        binding = check.targets.get(config.id)
        receipt_id = check.receipts.get(config.id)
        current = db.get(TargetReadiness, receipt_id) if receipt_id else None
        latest = (
            db.scalar(
                select(TargetReadiness)
                .where(
                    TargetReadiness.configuration_id == str(config.id),
                    TargetReadiness.configuration_digest == config.digest,
                    TargetReadiness.binding_digest == binding.digest,
                )
                .order_by(TargetReadiness.checked_at.desc())
                .limit(1)
            )
            if binding
            else None
        )
        targets.append(
            PreflightTarget(
                configuration_id=config.id,
                connection_name=config.connection_name,
                connection_kind=config.connection_kind,
                environment_id=config.environment_id,
                environment_name=config.environment_name,
                classification=config.content.classification,
                authority=target_authority(binding) if binding else None,
                readiness=readiness_view(current) if current else None,
                latest_readiness=readiness_view(latest) if latest else None,
            )
        )
    try:
        operations = validate_bindings(
            preparation.draft, preparation.configurations, preparation.scenario
        )
    except ValueError:
        operations = {}
    recoverable = {item.step_id for item in usable_recovery}
    window = preparation.draft.window
    return RunPreflightView(
        valid=not issues,
        checked_at=timestamp(now()),
        trigger=body.trigger,
        window_starts_at=window.starts_at.isoformat() if window else None,
        window_ends_at=window.ends_at.isoformat() if window else None,
        max_operations=manifest.max_operations,
        planned_attempts=planned_attempts(preparation, body.observations, body.recovery),
        approval_required=check.required if check.policy_resolved else None,
        environments=environments,
        targets=targets,
        recovery=[
            PreflightRecovery(
                step_id=step.id,
                label=step.label,
                mode="automatic" if step.id in recoverable else "manual",
            )
            for step in preparation.draft.steps
            if step.id in operations and operations[step.id].effect == "write"
        ],
        issues=issues,
        blockers=check.details,
    )


def authorize_run(
    db: Session, actor: Principal, run: ExerciseRun, state: ExerciseRunState, manifest: RunManifest
) -> None:
    check = check_authorization(
        db, actor, run, state, manifest, require_context=False, require_approval=False
    )
    if check.blockers:
        raise HTTPException(409, "; ".join(check.blockers))
    context = RunAuthorization(
        run_id=run.id,
        phase=state.phase,
        policy_versions=json.dumps(check.policies),
        binding_digest=check.binding_digest,
        target_bindings=json.dumps(
            [item.model_dump(mode="json") for item in check.targets.values()]
        ),
        readiness_ids=json.dumps(check.readiness),
        approval_required=check.required,
        actor=actor.object_id,
    )
    db.add(context)
    db.flush()
    state.context_id = context.id
    state.reason = None
    record_event(
        db,
        run,
        "run.authorized",
        {"actor": actor.object_id, "context_id": context.id, "approval_required": check.required},
    )


def decide_run(
    db: Session,
    actor: Principal,
    run: ExerciseRun,
    state: ExerciseRunState,
    manifest: RunManifest,
    body: RunApprovalInput,
) -> None:
    if not reviewer_allowed(db, actor, run):
        raise HTTPException(
            403,
            "Execution review requires a separately granted non-contributor who does not operate this run",
        )
    if body.manifest_digest != run.digest or str(body.context_id) != state.context_id:
        raise HTTPException(409, "Review the current manifest and authorization context")
    check = check_authorization(db, actor, run, state, manifest, require_approval=False)
    if check.blockers or not check.required:
        raise HTTPException(
            409, "; ".join(check.blockers) or "This environment policy does not require approval"
        )
    window = manifest.preparation.draft.window
    expiry = body.expires_at.astimezone(UTC).replace(tzinfo=None)
    if (
        not window
        or expiry < window.ends_at.replace(tzinfo=None)
        or not now() < expiry <= now() + timedelta(days=30)
    ):
        raise HTTPException(
            422, "Approval must cover the execution window and expire within thirty days"
        )
    grant = grant_record(db, run.workspace_id, actor.object_id, "reviewer")
    if grant is None or state.context_id is None:
        raise HTTPException(409, "Review authority changed")
    approval = RunApproval(
        context_id=state.context_id,
        reviewer=actor.object_id,
        grant_id=grant.id,
        decision=body.decision,
        sequence=state.version,
        expires_at=expiry,
        note=body.note,
    )
    db.add(approval)
    db.flush()
    record_event(
        db,
        run,
        "run.reviewed",
        {
            "approval_id": approval.id,
            "reviewer": actor.object_id,
            "decision": body.decision,
            "expires_at": timestamp(expiry),
            "note": body.note,
        },
    )
    state.version += 1


def revoke_approval(
    db: Session, actor: Principal, run: ExerciseRun, state: ExerciseRunState, approval_id: str
) -> None:
    approval = db.get(RunApproval, approval_id)
    context = db.get(RunAuthorization, approval.context_id) if approval else None
    if approval is None or context is None or context.run_id != run.id:
        raise HTTPException(404, "Execution approval not found")
    if approval.reviewer != actor.object_id and not is_admin(db, actor, fence=True):
        raise HTTPException(
            403, "Only the reviewer or an organization administrator may revoke approval"
        )
    if db.get(RunApprovalRevocation, approval.id) is None:
        db.add(RunApprovalRevocation(approval_id=approval.id, actor=actor.object_id))
        record_event(
            db, run, "run.approval_revoked", {"approval_id": approval.id, "actor": actor.object_id}
        )
        state.version += 1
        if state.active:
            notify_dispatch(db, run, state)


def dispatch_id(run: ExerciseRun, phase: str) -> str:
    return str(uuid5(UUID(run.id), phase))


def notify_dispatch(db: Session, run: ExerciseRun, state: ExerciseRunState) -> None:
    did = dispatch_id(run, state.phase)
    dispatch = db.get(RunDispatch, did)
    if dispatch is None:
        db.add(RunDispatch(id=did, run_id=run.id, phase=state.phase))
    else:
        dispatch.control_version += 1


def control_run(
    db: Session,
    actor: Principal,
    run: ExerciseRun,
    state: ExerciseRunState,
    manifest: RunManifest,
    body: RunControl,
) -> None:
    action = body.action
    require_operator(db, actor, run, original=action != "stop")
    if action == "authorize":
        if state.state not in {"prepared", "paused", "intervention"}:
            raise HTTPException(409, "Pause before replacing a run authorization context")
        authorize_run(db, actor, run, state, manifest)
    elif action == "pause":
        if state.state not in {"queued", "scheduled", "running", "waiting", "paused"}:
            raise HTTPException(409, "This run cannot be paused")
        state.state = "paused"
        notify_dispatch(db, run, state)
    elif action == "stop":
        if state.state not in {
            "prepared",
            "queued",
            "scheduled",
            "running",
            "waiting",
            "paused",
            "intervention",
            "stopping",
        }:
            raise HTTPException(409, "This run has already ended")
        state.stop_requested = True
        steps = list(
            db.scalars(
                select(RunStep).where(RunStep.run_id == run.id, RunStep.phase == state.phase)
            )
        )
        pending = False
        for step in steps:
            if step.state != "in_flight":
                continue
            if step.lease_until and step.lease_until > now():
                pending = True
            else:
                step.state, step.reason = (
                    "unknown",
                    "No confirmed outcome after an accepted attempt",
                )
                record_event(
                    db,
                    run,
                    "operation.unknown",
                    {"reason": step.reason, "phase": state.phase, "attempt_id": step.attempt_id},
                    step.step_id,
                )
        incomplete = any(step.state == "unknown" for step in steps)
        state.active = pending
        state.state = "stopping" if pending else ("stopped_incomplete" if incomplete else "stopped")
        state.reason = "Unconfirmed effects remain; stop did not undo them." if incomplete else None
        if pending:
            notify_dispatch(db, run, state)
    elif action == "recover":
        if (
            state.active
            or state.phase != "exercise"
            or state.state not in {"completed", "stopped", "failed"}
        ):
            raise HTTPException(409, "Finish and reconcile the exercise before preparing recovery")
        originals = list(
            db.scalars(select(RunStep).where(RunStep.run_id == run.id, RunStep.phase == "exercise"))
        )
        if any(step.state in {"unknown", "in_flight"} for step in originals):
            raise HTTPException(409, "Unconfirmed effects need reconciliation before recovery")
        operations = {str(item.id): item for item in manifest.preparation.draft.steps}
        configs = {item.id: item for item in manifest.preparation.configurations}
        recoverable = {str(item.step_id) for item in manifest.recovery}
        for step in originals:
            original = operations[step.step_id]
            if original.binding is None or step.state not in {"succeeded", "manually_accounted"}:
                continue
            config = configs[original.binding.configuration_id]
            operation = next(
                item
                for item in config.content.catalog.operations
                if (item.key, item.version)
                == (original.binding.operation_key, original.binding.operation_version)
            )
            if operation.effect == "write":
                db.add(
                    RunStep(
                        run_id=run.id,
                        phase="recovery",
                        step_id=step.step_id,
                        state="pending"
                        if step.step_id in recoverable and step.state == "succeeded"
                        else "manual_required",
                    )
                )
        state.phase, state.state, state.context_id = "recovery", "prepared", None
        state.stop_requested = False
    elif action in {"start", "resume", "reconcile"}:
        allowed = {"prepared"} if action == "start" else {"paused", "intervention"}
        if state.state not in allowed:
            raise HTTPException(409, "This action is not valid for the current run state")
        if state.stop_requested:
            raise HTTPException(
                409, "A stopped run cannot dispatch again; account for uncertain effects externally"
            )
        check = check_authorization(db, actor, run, state, manifest)
        if check.blockers:
            raise HTTPException(409, "; ".join(check.blockers))
        window = manifest.preparation.draft.window
        if (
            action == "start"
            and manifest.trigger == "manual"
            and window
            and now() < window.starts_at.replace(tzinfo=None)
        ):
            raise HTTPException(409, "Manual execution must start inside the approved window")
        problematic = list(
            db.scalars(
                select(RunStep).where(
                    RunStep.run_id == run.id,
                    RunStep.phase == state.phase,
                    RunStep.state.in_(["unknown", "failed", "rejected"]),
                )
            )
        )
        if problematic and action != "reconcile":
            raise HTTPException(
                409, "Reconcile uncertain or failed steps explicitly before resuming"
            )
        for step in problematic:
            if step.samples >= 3 or (step.lease_until and step.lease_until > now()):
                raise HTTPException(
                    409, "Attempt is still in flight or the bounded retry budget is exhausted"
                )
            binding, operation, _ = resolve_operation(manifest, step.step_id, state.phase)
            target = check.targets[binding.configuration_id]
            if operation.effect == "write" and not target.operation(operation).safe_replay:
                raise HTTPException(
                    409, "Target does not authorize safe replay; manual intervention is required"
                )
            step.state, step.reason = "pending", None
        active = db.scalar(
            select(ExerciseRunState.run_id).where(
                ExerciseRunState.board_id == run.board_id,
                ExerciseRunState.active == true(),
                ExerciseRunState.run_id != run.id,
            )
        )
        if active:
            raise HTTPException(409, "Another run is active on this board")
        state.active, state.state, state.reason = True, "queued", None
        notify_dispatch(db, run, state)
    state.version += 1
    record_event(
        db,
        run,
        f"control.{action}",
        {"actor": actor.object_id, "note": body.note, "phase": state.phase},
    )


def resolve_operation(
    manifest: RunManifest,
    step_id: str,
    phase: str,
) -> tuple[
    OperationBinding, OperationDefinition, dict[FieldName, Scalar | PriorResultReference | None]
]:
    parameters: dict[FieldName, Scalar | PriorResultReference | None]
    if phase == "recovery":
        recovery = next(item for item in manifest.recovery if str(item.step_id) == step_id)
        binding, parameters = recovery.binding, dict(recovery.parameters)
    else:
        step = next(item for item in manifest.preparation.draft.steps if str(item.id) == step_id)
        if step.binding is None:
            raise ValueError("Step has no operation binding")
        binding, parameters = step.binding, dict(step.parameters)
    config = next(
        item for item in manifest.preparation.configurations if item.id == binding.configuration_id
    )
    operation = next(
        item
        for item in config.content.catalog.operations
        if (item.key, item.version) == (binding.operation_key, binding.operation_version)
    )
    return binding, operation, parameters


def manual_recovery(
    db: Session,
    actor: Principal,
    run: ExerciseRun,
    state: ExerciseRunState,
    body: ManualRecoveryInput,
) -> None:
    require_operator(db, actor, run, original=False)
    step = db.get(RunStep, (run.id, body.phase, str(body.step_id)))
    stopped_unknown = (
        body.phase == "exercise"
        and state.state == "stopped_incomplete"
        and step is not None
        and step.state == "unknown"
    )
    recovery_report = (
        body.phase == "recovery"
        and state.phase == "recovery"
        and step is not None
        and step.state in {"manual_required", "rejected", "failed", "unknown"}
        and state.state in {"prepared", "paused", "intervention", "stopped", "stopped_incomplete"}
    )
    if not stopped_unknown and not recovery_report:
        raise HTTPException(
            409, "Only unresolved stopped effects or held recovery items accept an external report"
        )
    if step is None:
        raise HTTPException(409, "Evidence item is unavailable")
    record_event(
        db,
        run,
        "recovery.manual_report",
        {
            "actor": actor.object_id,
            "evidence_reference": body.evidence_reference,
            "note": body.note,
            "phase": body.phase,
        },
        str(body.step_id),
    )
    step.state = "manually_accounted"
    step.reason = "External operator report; not an automatically verified successful effect."
    db.flush()
    if (
        stopped_unknown
        and db.scalar(
            select(RunStep.step_id)
            .where(
                RunStep.run_id == run.id, RunStep.phase == "exercise", RunStep.state == "unknown"
            )
            .limit(1)
        )
        is None
    ):
        state.state = "stopped"
    if recovery_report:
        remaining = db.scalar(
            select(RunStep.step_id)
            .where(
                RunStep.run_id == run.id,
                RunStep.phase == "recovery",
                RunStep.state.not_in(["succeeded", "manually_accounted"]),
            )
            .limit(1)
        )
        if remaining is None:
            state.state, state.active, state.reason = "recovered_with_manual_reports", False, None
            record_event(db, run, "run.finished", {"phase": "recovery", "state": state.state})
    state.version += 1
