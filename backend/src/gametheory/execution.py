"""Executable contracts. Parsing history never consults the wall clock."""

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
        prep = self.preparation
        operations = validate_bindings(prep.draft, prep.configurations, prep.scenario)
        if not operations or prep.draft.window is None:
            raise ValueError("Execution needs operations and an explicit bounded window")
        if not prep.draft.recovery.strip():
            raise ValueError("Record a recovery decision before execution")
        if (
            sum(step.wait_seconds or 0 for step in prep.draft.steps)
            > (prep.draft.window.ends_at - prep.draft.window.starts_at).total_seconds()
        ):
            raise ValueError("Fixed waits cannot exceed the execution window")
        for config in prep.configurations:
            content = config.content
            if config.connection_kind not in {"sql", "rest"}:
                raise ValueError("Graph and MCP execution are not implemented")
            if content.classification == "unknown":
                raise ValueError("Target classification is unresolved")
            if not all([content.resource_id, content.endpoint, content.identity_ref]):
                raise ValueError("Execution targets must be complete")
            if config.connection_kind == "sql" and not content.database:
                raise ValueError("SQL execution requires an explicit database")
        for step in prep.draft.steps:
            if step.kind != "operation":
                continue
            operation = operations.get(step.id)
            if operation is None or operation.effect == "notify":
                raise ValueError("Every operation needs a supported exact binding")
            require_mutation_receipt(operation)
            for field in operation.parameters:
                if operation.invocation.kind == "sql" and field.name == "idempotency_key":
                    if step.parameters.get(field.name) is not None:
                        raise ValueError(
                            "Clear the SQL idempotency_key literal; execution explicitly owns that binding"
                        )
                    if (
                        field.type != "string"
                        or field.choices
                        or (field.max_length is not None and field.max_length < 64)
                    ):
                        raise ValueError(
                            "SQL idempotency_key must accommodate a dispatcher SHA-256 key"
                        )
                    continue
                if field.required and step.parameters.get(field.name) is None:
                    raise ValueError(f"Unresolved operation parameter: {field.name}")
        if len({item.step_id for item in self.observations}) != len(self.observations):
            raise ValueError("Only one observation policy is allowed per read step")
        for item in self.observations:
            operation = operations.get(item.step_id)
            if operation is None or operation.effect != "read":
                raise ValueError("Only registered read operations may be polled")
            observed_field = next(
                (field for field in operation.results if field.name == item.field), None
            )
            if observed_field is None:
                raise ValueError("Observation must compare a declared result")
            validate_scalar(observed_field, item.value, constraints=False)
            if item.operator not in {"eq", "ne"} and observed_field.type not in {
                "integer",
                "number",
                "datetime",
            }:
                raise ValueError("Ordered observation requires numeric or datetime output")
            if (
                item.timeout_seconds
                > (prep.draft.window.ends_at - prep.draft.window.starts_at).total_seconds()
            ):
                raise ValueError("Observation timeout exceeds the execution window")
        objective_ids = {item.id for item in prep.scenario.content.objectives}
        if len({item.objective_id for item in self.objectives}) != len(self.objectives):
            raise ValueError("Objective bindings must be unique")
        for rule in self.objectives:
            if rule.objective_id not in objective_ids:
                raise ValueError("Assessment must reference a pinned scenario objective")
            references = [(rule.step_id, rule.field)]
            if rule.anchor_step_id and rule.anchor_field:
                references.append((rule.anchor_step_id, rule.anchor_field))
            if rule.source_time_field:
                references.append((rule.step_id, rule.source_time_field))
            if isinstance(rule.value, PriorResultReference):
                references.append((rule.value.source_step_id, rule.value.field))
            for sid, field_name in references:
                operation = operations.get(sid)
                if operation is None or not any(
                    field.name == field_name for field in operation.results
                ):
                    raise ValueError("Objective evidence must reference declared operation results")
            compared_field = next(
                field for field in operations[rule.step_id].results if field.name == rule.field
            )
            if rule.operator not in {"eq", "ne"} and compared_field.type not in {
                "integer",
                "number",
                "datetime",
            }:
                raise ValueError("Ordered assessment requires numeric or datetime evidence")
            if isinstance(rule.value, PriorResultReference):
                value_field = next(
                    field
                    for field in operations[rule.value.source_step_id].results
                    if field.name == rule.value.field
                )
                if value_field.type != compared_field.type:
                    raise ValueError("Assessment comparison fields must have matching types")
            else:
                validate_scalar(compared_field, rule.value, constraints=False)
            clocks = []
            if rule.anchor_step_id and rule.anchor_field:
                clocks.append((rule.anchor_step_id, rule.anchor_field))
            if rule.source_time_field:
                clocks.append((rule.step_id, rule.source_time_field))
            for clock_step, clock_field in clocks:
                if (
                    next(
                        field
                        for field in operations[clock_step].results
                        if field.name == clock_field
                    ).type
                    != "datetime"
                ):
                    raise ValueError("Assessment clocks must reference declared datetime fields")
        configs = {item.id: item for item in prep.configurations}
        if len({item.step_id for item in self.recovery}) != len(self.recovery):
            raise ValueError("Only one recovery binding is allowed per effect")
        for recovery in self.recovery:
            source = operations.get(recovery.step_id)
            recovery_config = configs.get(recovery.binding.configuration_id)
            operation = (
                next(
                    (
                        item
                        for item in recovery_config.content.catalog.operations
                        if (item.key, item.version)
                        == (recovery.binding.operation_key, recovery.binding.operation_version)
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
                raise ValueError("Recovery must bind a registered write for a recorded write")
            require_mutation_receipt(operation)
            names = {field.name for field in operation.parameters}
            if not set(recovery.parameters) <= names:
                raise ValueError("Recovery parameters must be declared")
            for field in operation.parameters:
                value = recovery.parameters.get(field.name)
                if value is None and field.required and field.name != "idempotency_key":
                    raise ValueError("Recovery parameters must be complete")
                if isinstance(value, PriorResultReference):
                    origin = operations.get(value.source_step_id)
                    result = (
                        next(
                            (result for result in origin.results if result.name == value.field),
                            None,
                        )
                        if origin
                        else None
                    )
                    if result is None or not result.required or result.type != field.type:
                        raise ValueError("Recovery requires matching required recorded results")
                elif value is not None:
                    validate_scalar(field, value)
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
