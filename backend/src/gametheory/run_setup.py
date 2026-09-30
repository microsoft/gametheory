"""Reviewed run-check suggestions.

The run-check assistant only suggests observations, objective rules, and recovery
bindings for an operator to review in the guided run forms. It never creates,
authorizes, approves, or starts a run, never chooses notification recipients, and
never sees target metadata: its model context is minimized from the pinned preview.
"""

from collections import defaultdict, deque
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from gametheory.domain import Contract
from gametheory.execution import (
    ObjectiveRule,
    Observation,
    RecoveryBinding,
    execution_issues,
)
from gametheory.preparation import (
    Digest,
    FieldName,
    Identifier,
    Number,
    OperationDefinition,
    OperationField,
    PreparationManifest,
    PreparationStep,
    PriorResultReference,
    VersionIdentifier,
    validate_bindings,
)

MAX_PROMPT_CHARACTERS = 4000
# A bounded model input; larger preparations still use the forms directly.
MAX_CONTEXT_CHARACTERS = 200_000
NOTIFICATION_MESSAGE = (
    "Suggestions cannot use notification steps; notification routing is administrator-controlled"
)
RequestStatus = Literal["queued", "running", "proposed", "failed"]


class RunCheckRequestInput(Contract):
    preview_id: UUID
    preview_digest: Digest
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARACTERS)
    request_id: UUID

    @field_validator("prompt")
    @classmethod
    def bounded_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe what the run should check")
        # SQL Server bounds NVARCHAR by UTF-16 code units, not Python characters.
        if len(value.encode("utf-16-le")) > 2 * MAX_PROMPT_CHARACTERS:
            raise ValueError("Keep the request to 4,000 characters")
        return value


class RunCheckRequestAccepted(Contract):
    id: UUID
    status: RequestStatus


class DeclaredField(Contract):
    """A declared parameter or result: its shape only, never a value."""

    name: FieldName
    type: Literal["string", "integer", "number", "boolean", "uuid", "datetime"]
    required: bool
    choices: list[str] | None = None
    minimum: Number | None = None
    maximum: Number | None = None


class ContextOperation(Contract):
    label: str
    effect: Literal["read", "write", "notify"]
    parameters: list[DeclaredField]
    results: list[DeclaredField]


class ContextStep(Contract):
    id: UUID
    label: str
    kind: Literal["operation", "condition", "wait"]
    depends_on: list[UUID]
    may_not_run: bool
    operation: ContextOperation | None = None


class ContextObjective(Contract):
    id: UUID
    title: str
    criterion: str


class ContextRecoveryOperation(Contract):
    configuration_id: UUID
    key: Identifier
    version: VersionIdentifier
    label: str
    parameters: list[DeclaredField]
    results: list[DeclaredField]


class RunCheckContext(Contract):
    """Everything the model may see. Endpoints, resource and identity references,
    database and connection/environment names, token scopes, parameter values, and
    assets are structurally absent."""

    schema_version: Literal["run-check-context/v1"] = "run-check-context/v1"
    window_seconds: int | None
    steps: list[ContextStep]
    objectives: list[ContextObjective]
    recovery_operations: list[ContextRecoveryOperation]


Question = Annotated[str, Field(min_length=1, max_length=1000)]


class RunCheckSuggestion(Contract):
    """The only shape a model result may take. It has no field for a trigger, steps,
    recipients, grants, approvals, or dispatch, and unknown fields are rejected."""

    summary: str = Field(min_length=1, max_length=2000)
    observations: list[Observation] = Field(default_factory=list, max_length=100)
    objectives: list[ObjectiveRule] = Field(default_factory=list, max_length=500)
    recovery: list[RecoveryBinding] = Field(default_factory=list, max_length=100)
    questions: list[Question] = Field(default_factory=list, max_length=50)


class ReviewedObservation(Contract):
    item: Observation
    valid: bool
    issues: list[str]


class ReviewedObjective(Contract):
    item: ObjectiveRule
    valid: bool
    issues: list[str]


class ReviewedRecovery(Contract):
    item: RecoveryBinding
    valid: bool
    issues: list[str]


class RunCheckReview(Contract):
    observations: list[ReviewedObservation]
    objectives: list[ReviewedObjective]
    recovery: list[ReviewedRecovery]


class RunCheckSuggestionView(RunCheckReview):
    """A stored request and, once proposed, its suggestion reviewed against the pinned
    preview. Nothing here is applied or created until the operator does so."""

    id: UUID
    preview_id: UUID
    prompt: str
    status: RequestStatus
    error: str | None
    created_at: str
    summary: str | None
    questions: list[str]
    is_current: bool


def conditional_steps(steps: list[PreparationStep]) -> set[UUID]:
    """Steps on, or downstream of, a condition branch: they may not run."""

    following: dict[UUID, list[UUID]] = defaultdict(list)
    targets: list[UUID] = []
    for step in steps:
        for dependency in step.depends_on:
            following[dependency].append(step.id)
        if step.condition is not None:
            for target in step.condition.if_true + step.condition.if_false:
                following[step.id].append(target)
                targets.append(target)
    seen = set(targets)
    pending = deque(targets)
    while pending:
        for target in following[pending.popleft()]:
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return seen


def _declared(field: OperationField) -> DeclaredField:
    return DeclaredField(
        name=field.name,
        type=field.type,
        required=field.required,
        choices=field.choices,
        minimum=field.minimum,
        maximum=field.maximum,
    )


def _parameters(operation: OperationDefinition) -> list[DeclaredField]:
    # SQL idempotency keys are dispatcher-owned; a suggestion must never bind them.
    return [
        _declared(field)
        for field in operation.parameters
        if not (operation.invocation.kind == "sql" and field.name == "idempotency_key")
    ]


def run_check_context(prep: PreparationManifest) -> RunCheckContext:
    operations = validate_bindings(prep.draft, prep.configurations, prep.scenario)
    branched = conditional_steps(prep.draft.steps)
    window = prep.draft.window

    def operation_view(operation: OperationDefinition | None) -> ContextOperation | None:
        if operation is None:
            return None
        return ContextOperation(
            label=operation.label,
            effect=operation.effect,
            parameters=_parameters(operation),
            results=[_declared(field) for field in operation.results],
        )

    return RunCheckContext(
        window_seconds=int((window.ends_at - window.starts_at).total_seconds()) if window else None,
        steps=[
            ContextStep(
                id=step.id,
                label=step.label,
                kind=step.kind,
                depends_on=list(step.depends_on),
                may_not_run=step.id in branched,
                operation=operation_view(operations.get(step.id)),
            )
            for step in prep.draft.steps
        ],
        objectives=[
            ContextObjective(id=item.id, title=item.title, criterion=item.criterion)
            for item in prep.scenario.content.objectives
        ],
        recovery_operations=[
            ContextRecoveryOperation(
                configuration_id=config.id,
                key=operation.key,
                version=operation.version,
                label=operation.label,
                parameters=_parameters(operation),
                results=[_declared(field) for field in operation.results],
            )
            for config in prep.configurations
            for operation in config.content.catalog.operations
            if operation.effect == "write"
        ],
    )


def _referenced_steps(item: Observation | ObjectiveRule | RecoveryBinding) -> set[UUID]:
    steps = {item.step_id}
    if isinstance(item, ObjectiveRule):
        if item.anchor_step_id:
            steps.add(item.anchor_step_id)
        if isinstance(item.value, PriorResultReference):
            steps.add(item.value.source_step_id)
    if isinstance(item, RecoveryBinding):
        steps.update(
            value.source_step_id
            for value in item.parameters.values()
            if isinstance(value, PriorResultReference)
        )
    return steps


def review_suggestion(prep: PreparationManifest, suggestion: RunCheckSuggestion) -> RunCheckReview:
    """Validate every suggested item exactly as run creation would, located per item."""

    found: dict[tuple[str, int], list[str]] = defaultdict(list)
    for issue in execution_issues(
        prep, suggestion.observations, suggestion.objectives, suggestion.recovery
    ):
        if issue.section != "preparation" and issue.index is not None:
            messages = found[(issue.section, issue.index)]
            if issue.message not in messages:
                messages.append(issue.message)
    operations = validate_bindings(prep.draft, prep.configurations, prep.scenario)
    notifications = {sid for sid, operation in operations.items() if operation.effect == "notify"}
    located: list[tuple[str, int, Observation | ObjectiveRule | RecoveryBinding]] = [
        *(("observations", index, item) for index, item in enumerate(suggestion.observations)),
        *(("objectives", index, item) for index, item in enumerate(suggestion.objectives)),
        *(("recovery", index, item) for index, item in enumerate(suggestion.recovery)),
    ]
    for section, index, item in located:
        if _referenced_steps(item) & notifications:
            found[(section, index)].insert(0, NOTIFICATION_MESSAGE)

    def issues(section: str, index: int) -> list[str]:
        return found.get((section, index), [])

    return RunCheckReview(
        observations=[
            ReviewedObservation(
                item=item,
                valid=not issues("observations", index),
                issues=issues("observations", index),
            )
            for index, item in enumerate(suggestion.observations)
        ],
        objectives=[
            ReviewedObjective(
                item=item,
                valid=not issues("objectives", index),
                issues=issues("objectives", index),
            )
            for index, item in enumerate(suggestion.objectives)
        ],
        recovery=[
            ReviewedRecovery(
                item=item,
                valid=not issues("recovery", index),
                issues=issues("recovery", index),
            )
            for index, item in enumerate(suggestion.recovery)
        ],
    )
