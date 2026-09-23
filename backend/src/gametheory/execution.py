"""Executable contracts. Parsing history never consults the wall clock."""

from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from gametheory.domain import Contract
from gametheory.environment_policy import EnvironmentPolicyView
from gametheory.preparation import (
    Digest,
    ExplicitDatetime,
    FieldName,
    OperationBinding,
    OperationDefinition,
    PreparationManifest,
    PriorResultReference,
    Scalar,
    canonical_digest,
    validate_bindings,
    validate_scalar,
)

Comparator = Literal["eq", "ne", "gt", "gte", "lt", "lte"]
Outcome = Literal["succeeded", "rejected", "failed", "unknown"]
Phase = Literal["exercise", "recovery"]
Capability = Literal["operator", "reviewer"]


class ExecutionGrantInput(Contract):
    object_id: UUID
    capability: Capability


class ExecutionGrantView(ExecutionGrantInput):
    id: UUID
    workspace_id: UUID
    granted_by: UUID
    granted_at: str


class Observation(Contract):
    step_id: UUID
    field: FieldName
    operator: Comparator
    value: Scalar
    interval_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 10
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=86400)] = 600
    max_samples: Annotated[StrictInt, Field(ge=1, le=1000)] = 100


class ObjectiveRule(Contract):
    objective_id: UUID
    step_id: UUID
    field: FieldName
    operator: Comparator
    value: Scalar | PriorResultReference
    anchor_step_id: UUID | None = None
    anchor_field: FieldName | None = None
    within_seconds: Annotated[StrictInt, Field(ge=1, le=604800)] | None = None
    source_time_field: FieldName | None = None

    @model_validator(mode="after")
    def complete_clock(self) -> Self:
        if (
            len(
                {
                    value is None
                    for value in (self.anchor_step_id, self.anchor_field, self.within_seconds)
                }
            )
            != 1
        ):
            raise ValueError(
                "A timed objective needs an anchor step, timestamp field, and deadline"
            )
        return self


class RecoveryBinding(Contract):
    step_id: UUID
    binding: OperationBinding
    parameters: dict[FieldName, Scalar | PriorResultReference]
    ownership_parameter: FieldName
    version_parameter: FieldName

    @model_validator(mode="after")
    def owned_versioned_recovery(self) -> Self:
        if "idempotency_key" in self.parameters:
            raise ValueError("Recovery SQL idempotency keys are dispatcher-owned")
        if self.ownership_parameter == self.version_parameter:
            raise ValueError("Ownership and version preconditions must be separate")
        for name in (self.ownership_parameter, self.version_parameter):
            value = self.parameters.get(name)
            if not isinstance(value, PriorResultReference) or value.source_step_id != self.step_id:
                raise ValueError(
                    "Recovery must bind ownership and version from recorded operation results"
                )
        return self


class RunCreate(Contract):
    preview_id: UUID
    preview_digest: Digest
    trigger: Literal["manual", "scheduled"] = "manual"
    observations: list[Observation] = Field(default_factory=list, max_length=100)
    objectives: list[ObjectiveRule] = Field(default_factory=list, max_length=500)
    recovery: list[RecoveryBinding] = Field(default_factory=list, max_length=100)
    # Provenance only: recorded with the run.prepared event and audit, never in the manifest.
    suggestion_id: UUID | None = None


def require_mutation_receipt(operation: OperationDefinition) -> None:
    if operation.invocation.kind == "rest" and (operation.invocation.method == "GET") != (
        operation.effect == "read"
    ):
        raise ValueError("Only GET can be a REST read; mutating methods require a write contract")
    if operation.effect != "write":
        return
    fields = {item.name: item for item in operation.results}
    for name, kind in (
        ("outcome", "string"),
        ("durable_event_id", "uuid"),
        ("committed_at", "datetime"),
    ):
        field = fields.get(name)
        if field is None or not field.required or field.type != kind:
            raise ValueError(
                "Writes require declared outcome, durable_event_id, and committed_at receipt fields"
            )


IssueSection = Literal["preparation", "observations", "objectives", "recovery"]


class ExecutionIssue(Contract):
    """One reason a pinned preparation and its run bindings are not executable."""

    code: str
    message: str
    section: IssueSection
    index: int | None = None
    step_id: UUID | None = None
    objective_id: UUID | None = None
    configuration_id: UUID | None = None
    field: str | None = None


ORDERED_TYPES = {"integer", "number", "datetime"}


def _duplicates(keys: list[UUID]) -> set[int]:
    seen: set[UUID] = set()
    repeated = set()
    for index, key in enumerate(keys):
        if key in seen:
            repeated.add(index)
        seen.add(key)
    return repeated


def execution_issues(
    prep: PreparationManifest,
    observations: list[Observation],
    objectives: list[ObjectiveRule],
    recovery: list[RecoveryBinding],
) -> Iterator[ExecutionIssue]:
    """Yield every issue in validator order; RunManifest rejects the first one verbatim."""

    try:
        operations = validate_bindings(prep.draft, prep.configurations, prep.scenario)
    except ValueError as exc:
        yield ExecutionIssue(code="invalid_bindings", message=str(exc), section="preparation")
        return
    window = prep.draft.window
    span = (window.ends_at - window.starts_at).total_seconds() if window else None
    if not operations or window is None:
        yield ExecutionIssue(
            code="missing_operations_or_window",
            message="Execution needs operations and an explicit bounded window",
            section="preparation",
        )
    if not prep.draft.recovery.strip():
        yield ExecutionIssue(
            code="missing_recovery_decision",
            message="Record a recovery decision before execution",
            section="preparation",
        )
    if span is not None and sum(step.wait_seconds or 0 for step in prep.draft.steps) > span:
        yield ExecutionIssue(
            code="waits_exceed_window",
            message="Fixed waits cannot exceed the execution window",
            section="preparation",
        )
    for config in prep.configurations:
        content = config.content
        target_checks = (
            (
                config.connection_kind not in {"sql", "rest"},
                "unsupported_target_kind",
                "Graph and MCP execution are not implemented",
            ),
            (
                content.classification == "unknown",
                "unclassified_target",
                "Target classification is unresolved",
            ),
            (
                not all([content.resource_id, content.endpoint, content.identity_ref]),
                "incomplete_target",
                "Execution targets must be complete",
            ),
            (
                config.connection_kind == "sql" and not content.database,
                "missing_sql_database",
                "SQL execution requires an explicit database",
            ),
        )
        for failed, code, message in target_checks:
            if failed:
                yield ExecutionIssue(
                    code=code, message=message, section="preparation", configuration_id=config.id
                )
    for step in prep.draft.steps:
        if step.kind != "operation":
            continue
        operation = operations.get(step.id)
        if operation is None or operation.effect == "notify":
            yield ExecutionIssue(
                code="unsupported_operation",
                message="Every operation needs a supported exact binding",
                section="preparation",
                step_id=step.id,
            )
            continue
        try:
            require_mutation_receipt(operation)
        except ValueError as exc:
            yield ExecutionIssue(
                code="operation_receipt_contract",
                message=str(exc),
                section="preparation",
                step_id=step.id,
            )
        for field in operation.parameters:
            if operation.invocation.kind == "sql" and field.name == "idempotency_key":
                if step.parameters.get(field.name) is not None:
                    yield ExecutionIssue(
                        code="sql_idempotency_literal",
                        message="Clear the SQL idempotency_key literal; execution explicitly owns that binding",
                        section="preparation",
                        step_id=step.id,
                        field=field.name,
                    )
                elif (
                    field.type != "string"
                    or field.choices
                    or (field.max_length is not None and field.max_length < 64)
                ):
                    yield ExecutionIssue(
                        code="sql_idempotency_field_invalid",
                        message="SQL idempotency_key must accommodate a dispatcher SHA-256 key",
                        section="preparation",
                        step_id=step.id,
                        field=field.name,
                    )
                continue
            if field.required and step.parameters.get(field.name) is None:
                yield ExecutionIssue(
                    code="unresolved_parameter",
                    message=f"Unresolved operation parameter: {field.name}",
                    section="preparation",
                    step_id=step.id,
                    field=field.name,
                )
    yield from _observation_issues(operations, observations, span)
    yield from _objective_issues(prep, operations, objectives)
    yield from _recovery_issues(prep, operations, recovery)


def _at(template: ExecutionIssue, code: str, message: str, **changes: object) -> ExecutionIssue:
    return template.model_copy(update={"code": code, "message": message, **changes})


def _observation_issues(
    operations: dict[UUID, OperationDefinition],
    observations: list[Observation],
    span: float | None,
) -> Iterator[ExecutionIssue]:
    repeated = _duplicates([item.step_id for item in observations])
    for index in sorted(repeated):
        yield ExecutionIssue(
            code="duplicate_observation",
            message="Only one observation policy is allowed per read step",
            section="observations",
            index=index,
            step_id=observations[index].step_id,
        )
    for index, item in enumerate(observations):
        if index in repeated:
            continue
        here = ExecutionIssue(
            code="",
            message="",
            section="observations",
            index=index,
            step_id=item.step_id,
            field=item.field,
        )
        operation = operations.get(item.step_id)
        if operation is None or operation.effect != "read":
            yield _at(here, "observation_not_read", "Only registered read operations may be polled")
            continue
        observed_field = next(
            (field for field in operation.results if field.name == item.field), None
        )
        if observed_field is None:
            yield _at(
                here, "observation_field_undeclared", "Observation must compare a declared result"
            )
            continue
        try:
            validate_scalar(observed_field, item.value, constraints=False)
        except ValueError as exc:
            yield _at(here, "observation_value_invalid", str(exc))
            continue
        if item.operator not in {"eq", "ne"} and observed_field.type not in ORDERED_TYPES:
            yield _at(
                here,
                "observation_ordered_type",
                "Ordered observation requires numeric or datetime output",
            )
        if span is not None and item.timeout_seconds > span:
            yield _at(
                here,
                "observation_timeout_exceeds_window",
                "Observation timeout exceeds the execution window",
            )


def _objective_issues(
    prep: PreparationManifest,
    operations: dict[UUID, OperationDefinition],
    objectives: list[ObjectiveRule],
) -> Iterator[ExecutionIssue]:
    objective_ids = {item.id for item in prep.scenario.content.objectives}
    repeated = _duplicates([item.objective_id for item in objectives])
    for index in sorted(repeated):
        yield ExecutionIssue(
            code="duplicate_objective_rule",
            message="Objective bindings must be unique",
            section="objectives",
            index=index,
            objective_id=objectives[index].objective_id,
        )
    for index, rule in enumerate(objectives):
        if index in repeated:
            continue
        here = ExecutionIssue(
            code="",
            message="",
            section="objectives",
            index=index,
            step_id=rule.step_id,
            objective_id=rule.objective_id,
            field=rule.field,
        )
        if rule.objective_id not in objective_ids:
            yield _at(
                here,
                "objective_unknown",
                "Assessment must reference a pinned scenario objective",
            )
            continue
        references = [(rule.step_id, rule.field)]
        if rule.anchor_step_id and rule.anchor_field:
            references.append((rule.anchor_step_id, rule.anchor_field))
        if rule.source_time_field:
            references.append((rule.step_id, rule.source_time_field))
        if isinstance(rule.value, PriorResultReference):
            references.append((rule.value.source_step_id, rule.value.field))
        undeclared = [
            (sid, name)
            for sid, name in references
            if sid not in operations
            or not any(field.name == name for field in operations[sid].results)
        ]
        if undeclared:
            yield _at(
                here,
                "objective_reference_undeclared",
                "Objective evidence must reference declared operation results",
                step_id=undeclared[0][0],
                field=undeclared[0][1],
            )
            continue
        compared_field = next(
            field for field in operations[rule.step_id].results if field.name == rule.field
        )
        if rule.operator not in {"eq", "ne"} and compared_field.type not in ORDERED_TYPES:
            yield _at(
                here,
                "objective_ordered_type",
                "Ordered assessment requires numeric or datetime evidence",
            )
        if isinstance(rule.value, PriorResultReference):
            value_field = next(
                field
                for field in operations[rule.value.source_step_id].results
                if field.name == rule.value.field
            )
            if value_field.type != compared_field.type:
                yield _at(
                    here,
                    "objective_value_type_mismatch",
                    "Assessment comparison fields must have matching types",
                )
        else:
            try:
                validate_scalar(compared_field, rule.value, constraints=False)
            except ValueError as exc:
                yield _at(here, "objective_value_invalid", str(exc))
        clocks = []
        if rule.anchor_step_id and rule.anchor_field:
            clocks.append((rule.anchor_step_id, rule.anchor_field))
        if rule.source_time_field:
            clocks.append((rule.step_id, rule.source_time_field))
        for clock_step, clock_field in clocks:
            clock = next(
                field for field in operations[clock_step].results if field.name == clock_field
            )
            if clock.type != "datetime":
                yield _at(
                    here,
                    "objective_clock_not_datetime",
                    "Assessment clocks must reference declared datetime fields",
                    step_id=clock_step,
                    field=clock_field,
                )
                break


def _recovery_issues(
    prep: PreparationManifest,
    operations: dict[UUID, OperationDefinition],
    recovery: list[RecoveryBinding],
) -> Iterator[ExecutionIssue]:
    configs = {item.id: item for item in prep.configurations}
    repeated = _duplicates([item.step_id for item in recovery])
    for index in sorted(repeated):
        yield ExecutionIssue(
            code="duplicate_recovery",
            message="Only one recovery binding is allowed per effect",
            section="recovery",
            index=index,
            step_id=recovery[index].step_id,
        )
    for index, binding in enumerate(recovery):
        if index in repeated:
            continue
        here = ExecutionIssue(
            code="",
            message="",
            section="recovery",
            index=index,
            step_id=binding.step_id,
            configuration_id=binding.binding.configuration_id,
        )
        source = operations.get(binding.step_id)
        recovery_config = configs.get(binding.binding.configuration_id)
        operation = (
            next(
                (
                    item
                    for item in recovery_config.content.catalog.operations
                    if (item.key, item.version)
                    == (binding.binding.operation_key, binding.binding.operation_version)
                ),
                None,
            )
            if recovery_config
            else None
        )
        if (
            source is None
            or source.effect != "write"
            or operation is None
            or operation.effect != "write"
        ):
            yield _at(
                here,
                "recovery_binding_invalid",
                "Recovery must bind a registered write for a recorded write",
            )
            continue
        try:
            require_mutation_receipt(operation)
        except ValueError as exc:
            yield _at(here, "recovery_receipt_contract", str(exc))
            continue
        names = {field.name for field in operation.parameters}
        if not set(binding.parameters) <= names:
            yield _at(here, "recovery_parameter_undeclared", "Recovery parameters must be declared")
            continue
        for field in operation.parameters:
            value = binding.parameters.get(field.name)
            if value is None and field.required and field.name != "idempotency_key":
                yield _at(
                    here,
                    "recovery_parameter_incomplete",
                    "Recovery parameters must be complete",
                    field=field.name,
                )
            elif isinstance(value, PriorResultReference):
                origin = operations.get(value.source_step_id)
                result = (
                    next((item for item in origin.results if item.name == value.field), None)
                    if origin
                    else None
                )
                if result is None or not result.required or result.type != field.type:
                    yield _at(
                        here,
                        "recovery_reference_invalid",
                        "Recovery requires matching required recorded results",
                        field=field.name,
                    )
            elif value is not None:
                try:
                    validate_scalar(field, value)
                except ValueError as exc:
                    yield _at(here, "recovery_value_invalid", str(exc), field=field.name)


def planned_attempts(
    prep: PreparationManifest, observations: list[Observation], recovery: list[RecoveryBinding]
) -> int:
    """Worst-case exercise and recovery attempts, excluding explicit reconciliation retries."""

    sampled = {item.step_id: item.max_samples for item in observations}
    return sum(
        sampled.get(step.id, 1) for step in prep.draft.steps if step.kind == "operation"
    ) + len(recovery)


class RunManifest(Contract):
    schema_version: Literal["exercise-execution/v2"] = "exercise-execution/v2"
    run_id: UUID
    preparation: PreparationManifest
    trigger: Literal["manual", "scheduled"]
    observations: list[Observation]
    objectives: list[ObjectiveRule]
    recovery: list[RecoveryBinding]
    sql_idempotency: Literal["dispatcher-owned/v1"] = "dispatcher-owned/v1"
    max_operations: Annotated[StrictInt, Field(ge=1, le=1000)] = 1000

    @model_validator(mode="after")
    def executable(self) -> Self:
        for issue in execution_issues(
            self.preparation, self.observations, self.objectives, self.recovery
        ):
            raise ValueError(issue.message)
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


class RunApprovalInput(Contract):
    context_id: UUID
    manifest_digest: Digest
    decision: Literal["approved", "rejected"]
    expires_at: ExplicitDatetime
    note: str = Field(min_length=1, max_length=2000)


class RunControl(Contract):
    action: Literal["authorize", "start", "pause", "resume", "stop", "reconcile", "recover"]
    note: str = Field(min_length=1, max_length=2000)


class ManualRecoveryInput(Contract):
    step_id: UUID
    phase: Phase = "recovery"
    evidence_reference: str = Field(
        min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_./:@()+-]+$"
    )
    note: str = Field(min_length=1, max_length=2000)


class ReadinessReceipt(Contract):
    configuration_id: UUID
    binding_digest: Digest
    checked_at: ExplicitDatetime
    expires_at: ExplicitDatetime
    evidence_reference: str = Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_./:@()-]+$")
    checks: list[Literal["connectivity", "identity", "permissions", "isolation"]]

    @model_validator(mode="after")
    def complete_receipt(self) -> Self:
        if (
            set(self.checks) != {"connectivity", "identity", "permissions", "isolation"}
            or len(self.checks) != 4
        ):
            raise ValueError(
                "Readiness must account for connectivity, identity, permissions, and isolation"
            )
        if not self.checked_at < self.expires_at:
            raise ValueError("Readiness must have an explicit positive lifetime")
        return self


class RunStepView(Contract):
    step_id: str
    phase: Phase
    state: str
    result: dict[str, Scalar | None]
    reason: str | None
    samples: int
    started_at: str | None
    finished_at: str | None


class RunEventView(Contract):
    id: UUID
    kind: str
    step_id: str | None
    created_at: str
    detail: dict[str, Scalar | None]


class ObjectiveFinding(Contract):
    objective_id: UUID
    state: Literal["met", "unmet", "indeterminate"]
    reason: str
    evidence_ids: list[UUID]


class TargetAuthorityView(Contract):
    configuration_id: UUID
    resource_id: str
    endpoint: str
    database: str
    identity_ref: str
    client_id: UUID
    token_scope: str
    operation_digests: list[Digest]
    replayable_operations: list[Digest]


class ReadinessView(Contract):
    id: UUID
    configuration_id: UUID
    checked_at: str
    expires_at: str
    evidence_reference: str
    operator: str


class RunAuthorizationView(Contract):
    id: UUID
    created_by: UUID
    created_at: str
    policies: list[EnvironmentPolicyView]
    targets: list[TargetAuthorityView]
    readiness: list[ReadinessView]


BlockerRemedy = Literal[
    "workspace_access",
    "run_access",
    "runtime",
    "target_bindings",
    "preparation",
    "environment_policy",
    "readiness",
    "assets",
    "authorize",
    "approval",
    "run_state",
]


class RunBlocker(Contract):
    """A current reason an action is unavailable and who can resolve it."""

    code: str
    message: str
    remedy: BlockerRemedy
    environment_id: UUID | None = None
    configuration_id: UUID | None = None


class PreflightTarget(Contract):
    configuration_id: UUID
    connection_name: str
    connection_kind: Literal["sql", "rest", "graph"]
    environment_id: UUID
    environment_name: str
    classification: Literal["unknown", "nonproduction", "production"]
    authority: TargetAuthorityView | None
    readiness: ReadinessView | None
    latest_readiness: ReadinessView | None


class PreflightRecovery(Contract):
    step_id: UUID
    label: str
    mode: Literal["automatic", "manual"]


class RunPreflightView(Contract):
    """A non-mutating evaluation of a proposed run; it is never execution authority."""

    valid: bool
    checked_at: str
    trigger: Literal["manual", "scheduled"]
    window_starts_at: str | None
    window_ends_at: str | None
    max_operations: int
    planned_attempts: int
    approval_required: bool | None
    environments: list[EnvironmentPolicyView]
    targets: list[PreflightTarget]
    recovery: list[PreflightRecovery]
    issues: list[ExecutionIssue]
    blockers: list[RunBlocker]


class RunView(Contract):
    id: UUID
    board_id: UUID
    version: int
    state: str
    phase: Phase
    operator: UUID
    created_at: str
    manifest_digest: Digest
    manifest: RunManifest
    context_id: UUID | None
    authorization: RunAuthorizationView | None
    approval_required: bool | None
    approval_status: Literal[
        "not_required", "required", "approved", "rejected", "invalid", "unresolved"
    ]
    blockers: list[str]
    blocker_details: list[RunBlocker]
    can_operate: bool
    can_stop: bool
    can_review: bool
    steps: list[RunStepView]
    events: list[RunEventView]
    findings: list[ObjectiveFinding]


class RunSummary(Contract):
    id: UUID
    board_id: UUID
    state: str
    phase: Phase
    operator: UUID
    created_at: str


def compare(
    left: Scalar, operator: Comparator, right: Scalar, data_type: str | None = None
) -> bool:
    # Do not coerce booleans/strings to numbers when evaluating runtime evidence.
    if isinstance(left, bool) != isinstance(right, bool):
        raise ValueError("Comparison types differ")
    if isinstance(left, str) != isinstance(right, str):
        raise ValueError("Comparison types differ")
    if (
        isinstance(left, str)
        and isinstance(right, str)
        and (data_type == "datetime" or operator not in {"eq", "ne"})
    ):
        a, b = datetime.fromisoformat(left), datetime.fromisoformat(right)
        if a.tzinfo is None or b.tzinfo is None:
            raise ValueError("Comparison timestamps require timezones")
        return {"eq": a == b, "ne": a != b, "gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[
            operator
        ]
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    if isinstance(left, bool) or isinstance(right, bool):
        raise ValueError("Booleans do not support ordered comparison")
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        raise ValueError("Ordered comparison needs numbers or timestamps")
    return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[
        operator
    ]
