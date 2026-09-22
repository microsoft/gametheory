"""Pure, non-executable preparation contracts and static validation."""

import hashlib
import ipaddress
import json
import math
import re
from collections import deque
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BeforeValidator,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from gametheory.domain import Contract, Name, ScenarioContent

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")]
VersionIdentifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")]
FieldName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Scalar = (
    Annotated[StrictStr, Field(max_length=4000)]
    | StrictInt
    | Annotated[StrictFloat, Field(allow_inf_nan=False)]
    | StrictBool
)
Number = StrictInt | Annotated[StrictFloat, Field(allow_inf_nan=False)]
MAX_STEPS = 200
MAX_EDGES = 1000
MAX_WAIT_SECONDS = 86400
MAX_WINDOW_SECONDS = 604800
MAX_BRANCH_ALTERNATIVES = 256
MAX_BRANCH_ANALYSIS_WORK = 16384
BranchGuard = frozenset[tuple[UUID, bool]]


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def canonical_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def strict_json(value: str | bytes) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON object keys are not permitted")
            result[key] = item
        return result

    def nonfinite(_: str) -> None:
        raise ValueError("Non-finite JSON numbers are not permitted")

    result: object = json.loads(value, object_pairs_hook=unique, parse_constant=nonfinite)

    def valid_values(item: object) -> None:
        if isinstance(item, str):
            item.encode("utf-8")
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Non-finite JSON numbers are not permitted")
        elif isinstance(item, list):
            for child in item:
                valid_values(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                valid_values(key)
                valid_values(child)

    valid_values(result)
    return result


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit timezone is required")
    return value.astimezone(UTC)


def explicit_datetime(value: object) -> datetime:
    if isinstance(value, str):
        return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    if isinstance(value, datetime):
        return aware_utc(value)
    raise ValueError("Supply a datetime with an explicit timezone, not an inferred timestamp")


ExplicitDatetime = Annotated[AwareDatetime, BeforeValidator(explicit_datetime)]


def safe_endpoint(value: str) -> str:
    if not value:
        return value
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value or "%" in value:
        raise ValueError("Endpoint must be a literal, credential-free base URL")
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
        or any(part in {".", ".."} for part in parsed.path.split("/"))
        or "//" in parsed.path
    ):
        raise ValueError("Endpoint must not contain credentials, traversal, queries or fragments")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Endpoint port is invalid") from exc
    if parsed.scheme == "https":
        return value
    if parsed.scheme == "http" and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
        return value
    raise ValueError("Use HTTPS, or explicit loopback HTTP for local development")


def sql_hostname(value: str) -> str:
    if not value:
        return value
    if "%" in value:
        raise ValueError("SQL endpoint metadata must not contain encoded or scoped components")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if (
            not re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*",
                value,
            )
            or len(value) > 253
        ):
            raise ValueError("SQL endpoint metadata must be a hostname or IP address") from None
    return value


class OperationField(Contract):
    name: FieldName
    type: Literal["string", "integer", "number", "boolean", "uuid", "datetime"]
    required: StrictBool = True
    minimum: Number | None = None
    maximum: Number | None = None
    max_length: Annotated[StrictInt, Field(ge=1, le=4000)] | None = None
    choices: list[Annotated[StrictStr, Field(max_length=4000)]] | None = Field(
        default=None, min_length=1, max_length=100
    )

    @model_validator(mode="after")
    def constraints_match_type(self) -> Self:
        if self.type not in {"integer", "number"} and (
            self.minimum is not None or self.maximum is not None
        ):
            raise ValueError("Numeric bounds apply only to numeric fields")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Minimum cannot exceed maximum")
        if self.type != "string" and (self.max_length is not None or self.choices is not None):
            raise ValueError("Length and choices apply only to string fields")
        if self.choices is not None:
            if len(self.choices) != len(set(self.choices)):
                raise ValueError("Choices must be unique")
            if self.max_length is not None and any(
                len(value) > self.max_length for value in self.choices
            ):
                raise ValueError("Choices must respect max_length")
        return self


class SqlInvocation(Contract):
    kind: Literal["sql"]
    procedure: Annotated[
        str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}\.[A-Za-z_][A-Za-z0-9_]{0,127}$")
    ]


class RestInvocation(Contract):
    """Description only: expected_version maps to future strong If-Match.

    It is a string with explicit max_length and a bare, unquoted opaque value.
    The future dispatcher owns Idempotency-Key; catalogs cannot supply headers.
    Remaining non-path parameters are GET query fields or a flat write body.
    """

    kind: Literal["rest"]
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=500)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        if value == "/":
            return value
        if not value.startswith("/") or value.startswith("//") or "//" in value:
            raise ValueError("A single root-relative path is required")
        segments = value[1:].split("/")
        if segments[-1] == "":
            segments.pop()
        if any(
            segment in {"", ".", ".."}
            or not re.fullmatch(r"(?:[A-Za-z0-9_.~-]+|\{[A-Za-z_][A-Za-z0-9_]{0,63}\})", segment)
            for segment in segments
        ):
            raise ValueError(
                "Only literal path segments and whole parameter placeholders are allowed"
            )
        return value


class GraphInvocation(Contract):
    kind: Literal["graph"]
    template_key: Identifier


Invocation = Annotated[
    SqlInvocation | RestInvocation | GraphInvocation, Field(discriminator="kind")
]
_CREDENTIAL_FIELDS = {
    "password",
    "passwd",
    "secret",
    "clientsecret",
    "token",
    "accesstoken",
    "refreshtoken",
    "apikey",
    "authorization",
    "headers",
    "connectionstring",
}
_MESSAGE_FIELDS = {
    "sender",
    "from",
    "recipients",
    "to",
    "cc",
    "bcc",
    "replyto",
    "attachments",
    "body",
    "message",
    "subject",
    "html",
    "content",
    "trustedlink",
    "fromaddress",
    "senderaddress",
    "senderemail",
    "sendermailbox",
    "recipient",
    "recipientaddress",
    "recipientaddresses",
    "recipientemail",
    "torecipients",
    "ccrecipients",
    "bccrecipients",
    "replytoaddress",
    "attachment",
    "attachmentids",
    "messagebody",
    "messagecontent",
    "htmlbody",
    "bodyhtml",
    "textbody",
    "plaintextbody",
    "subjectline",
}


class OperationDefinition(Contract):
    key: Identifier
    version: VersionIdentifier
    label: Name
    effect: Literal["read", "write", "notify"]
    invocation: Invocation
    parameters: list[OperationField] = Field(max_length=100)
    results: list[OperationField] = Field(max_length=100)
    recovery: str = Field(min_length=1, max_length=4000, pattern=r".*\S.*")

    @model_validator(mode="after")
    def restricted_operation(self) -> Self:
        for fields in (self.parameters, self.results):
            names = [field.name for field in fields]
            if len(names) != len(set(names)):
                raise ValueError("Operation field names must be unique")
            if any(re.sub(r"[^a-z]", "", name.lower()) in _CREDENTIAL_FIELDS for name in names):
                raise ValueError("Operation fields cannot carry credentials or arbitrary headers")
        if isinstance(self.invocation, RestInvocation):
            declared_names = {field.name for field in self.parameters}
            placeholders = set(re.findall(r"\{([^}]+)\}", self.invocation.path))
            if not placeholders <= declared_names:
                raise ValueError("Path placeholders must name declared parameters")
            if any(not field.required for field in self.parameters if field.name in placeholders):
                raise ValueError("Path parameters must be required")
            if self.invocation.method == "GET" and self.effect != "read":
                raise ValueError("GET operations must be read-only descriptions")
            for field in self.parameters:
                if re.sub(r"[^a-z]", "", field.name.lower()) in {"idempotencykey", "ifmatch"}:
                    raise ValueError(
                        "Idempotency-Key is dispatcher-owned; use expected_version for If-Match"
                    )
                if field.name == "expected_version":
                    if field.type != "string" or field.max_length is None:
                        raise ValueError(
                            "REST expected_version must be an explicitly bounded string"
                        )
                    if field.name in placeholders:
                        raise ValueError(
                            "REST expected_version is a header control, not a path field"
                        )
                    for choice in field.choices or []:
                        validate_expected_version(choice)
        if isinstance(self.invocation, GraphInvocation):
            if self.effect != "notify":
                raise ValueError("Fixed notification templates must have notify effect")
            if any(
                re.sub(r"[^a-z]", "", field.name.lower()) in _MESSAGE_FIELDS
                for field in self.parameters
            ):
                raise ValueError(
                    "Notification routing and message content are administrator-controlled"
                )
        return self


class OperationCatalog(Contract):
    schema_version: Literal["operation-catalog/v1"] = "operation-catalog/v1"
    name: Name
    operations: list[OperationDefinition] = Field(max_length=200)

    @model_validator(mode="after")
    def unique_operations(self) -> Self:
        keys = [(operation.key, operation.version) for operation in self.operations]
        if len(keys) != len(set(keys)):
            raise ValueError("Operation key/version pairs must be unique")
        return self


class NotificationConfiguration(Contract):
    template_asset_id: UUID | None = None
    sender: str = Field(default="", max_length=254)
    recipients: list[Annotated[str, Field(max_length=254)]] = Field(
        default_factory=list, max_length=100
    )
    trusted_link: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def fixed_routing(self) -> Self:
        for address in ([self.sender] if self.sender else []) + self.recipients:
            if not re.fullmatch(
                r"[A-Za-z0-9.!#$&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?",
                address,
            ):
                raise ValueError(
                    "Use a single explicit mailbox per entry, without display names or headers"
                )
        if len({address.lower() for address in self.recipients}) != len(self.recipients):
            raise ValueError("Recipients must be unique")
        safe_endpoint(self.trusted_link)
        return self


class ConnectionConfiguration(Contract):
    schema_version: Literal["connection-configuration/v1"] = "connection-configuration/v1"
    classification: Literal["unknown", "nonproduction", "production"] = "unknown"
    resource_id: str = Field(default="", max_length=512)
    endpoint: str = Field(default="", max_length=1000)
    database: str = Field(default="", max_length=128)
    identity_ref: str = Field(default="", max_length=512)
    catalog: OperationCatalog
    notification: NotificationConfiguration | None = None

    @field_validator("endpoint")
    @classmethod
    def noncredential_endpoint(cls, value: str) -> str:
        return safe_endpoint(value) if "://" in value else sql_hostname(value)

    @field_validator("resource_id", "identity_ref")
    @classmethod
    def opaque_reference(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_./:@()+-]+", value):
            raise ValueError("Use a non-secret resource or identity reference, not credentials")
        return value

    @field_validator("database")
    @classmethod
    def database_name(cls, value: str) -> str:
        if value and not re.fullmatch(r"[A-Za-z0-9_. -]+", value):
            raise ValueError("Use a database name, not a connection string")
        return value


def validate_configuration_kind(content: ConnectionConfiguration, kind: str) -> None:
    if kind not in {"sql", "rest", "graph"}:
        raise ValueError("This inventory kind does not support preparation bindings")
    if any(operation.invocation.kind != kind for operation in content.catalog.operations):
        raise ValueError("All operations must match the inventory connection kind")
    if kind == "sql":
        sql_hostname(content.endpoint)
    else:
        safe_endpoint(content.endpoint)
        if content.database:
            raise ValueError("Database metadata applies only to SQL connections")
    if kind != "graph" and content.notification is not None:
        raise ValueError("Notification routing applies only to fixed Graph templates")


class OperationBinding(Contract):
    configuration_id: UUID
    operation_key: Identifier
    operation_version: VersionIdentifier


class PriorResultReference(Contract):
    """A required declared result of a guaranteed earlier operation; never evaluated here."""

    source_step_id: UUID
    field: FieldName


class StepCondition(Contract):
    source_step_id: UUID
    result_field: FieldName
    operator: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    value: Scalar
    if_true: list[UUID] = Field(default_factory=list, max_length=MAX_STEPS)
    if_false: list[UUID] = Field(default_factory=list, max_length=MAX_STEPS)


class PreparationStep(Contract):
    id: UUID
    label: Name
    kind: Literal["operation", "condition", "wait"]
    authoring_node_id: UUID | None = None
    depends_on: list[UUID] = Field(default_factory=list, max_length=MAX_STEPS)
    binding: OperationBinding | None = None
    parameters: dict[FieldName, Scalar | PriorResultReference | None] = Field(
        default_factory=dict, max_length=100
    )
    wait_seconds: Annotated[StrictInt, Field(ge=1, le=MAX_WAIT_SECONDS)] | None = None
    condition: StepCondition | None = None

    @model_validator(mode="after")
    def fields_match_kind(self) -> Self:
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("Dependencies must be unique")
        if self.kind != "operation" and (self.binding is not None or self.parameters):
            raise ValueError("Only operation steps may have bindings and parameters")
        if (self.kind == "wait") != (self.wait_seconds is not None):
            raise ValueError("Only wait steps require a finite wait_seconds")
        if (self.kind == "condition") != (self.condition is not None):
            raise ValueError("Only condition steps require a condition")
        return self


class PreparationWindow(Contract):
    starts_at: ExplicitDatetime
    ends_at: ExplicitDatetime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return aware_utc(value)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        duration = (self.ends_at - self.starts_at).total_seconds()
        if not 0 < duration <= MAX_WINDOW_SECONDS:
            raise ValueError("The explicit window must be positive and at most seven days")
        return self


class BoardDraft(Contract):
    schema_version: Literal["exercise-preparation-draft/v1"] = "exercise-preparation-draft/v1"
    name: Name
    steps: list[PreparationStep] = Field(default_factory=list, max_length=MAX_STEPS)
    window: PreparationWindow | None = None
    notification_budget: Annotated[StrictInt, Field(ge=0, le=100)] = 0
    recovery: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def bounded_acyclic_graph(self) -> Self:
        guaranteed_predecessors(self.steps)
        if sum(step.wait_seconds or 0 for step in self.steps) > MAX_WINDOW_SECONDS:
            raise ValueError("Total proposed waits must be bounded to at most seven days")
        return self


def graph_edges(steps: list[PreparationStep]) -> dict[UUID, set[UUID]]:
    ids = {step.id for step in steps}
    if len(ids) != len(steps):
        raise ValueError("Step identifiers must be unique")
    edges: dict[UUID, set[UUID]] = {step_id: set() for step_id in ids}
    for step in steps:
        for dependency in step.depends_on:
            if dependency not in ids or dependency == step.id:
                raise ValueError("Dependencies must refer to other steps in this preparation")
            edges[dependency].add(step.id)
        if step.condition is not None:
            condition = step.condition
            branches = condition.if_true + condition.if_false
            if not branches or len(branches) != len(set(branches)):
                raise ValueError(
                    "Condition branches must be nonempty in total, unique and disjoint"
                )
            if any(target not in ids or target == step.id for target in branches):
                raise ValueError("Branches must refer to other steps in this preparation")
            edges[step.id].update(branches)
    if sum(map(len, edges.values())) > MAX_EDGES:
        raise ValueError("The preparation flow has too many dependencies and branches")
    indegree = dict.fromkeys(ids, 0)
    for targets in edges.values():
        for target in targets:
            indegree[target] += 1
    pending = deque(step_id for step_id in ids if indegree[step_id] == 0)
    visited = 0
    while pending:
        step_id = pending.popleft()
        visited += 1
        for target in edges[step_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                pending.append(target)
    if visited != len(ids):
        raise ValueError("Preparation dependencies and branches must be acyclic")
    return edges


def guaranteed_predecessors(steps: list[PreparationStep]) -> dict[UUID, set[UUID]]:
    edges = graph_edges(steps)
    by_id = {step.id: step for step in steps}
    controls: dict[UUID, list[tuple[UUID, bool]]] = {step_id: [] for step_id in edges}
    indegree = dict.fromkeys(edges, 0)
    for targets in edges.values():
        for target in targets:
            indegree[target] += 1
    for step in steps:
        if step.condition is not None:
            for outcome, branch_targets in (
                (True, step.condition.if_true),
                (False, step.condition.if_false),
            ):
                for target in branch_targets:
                    controls[target].append((step.id, outcome))
    guaranteed: dict[UUID, set[UUID]] = {}
    guards: dict[UUID, set[BranchGuard]] = {}
    analysis_work = 0

    def combine(left: set[BranchGuard], right: set[BranchGuard]) -> set[BranchGuard]:
        nonlocal analysis_work
        combined: set[BranchGuard] = set()
        for first in left:
            for second in right:
                analysis_work += 1
                if analysis_work > MAX_BRANCH_ANALYSIS_WORK:
                    raise ValueError("Conditional paths exceed the bounded static analysis budget")
                if any((condition_id, not outcome) in second for condition_id, outcome in first):
                    continue
                combined.add(first | second)
                if len(combined) > MAX_BRANCH_ALTERNATIVES:
                    raise ValueError(
                        "Too many alternative conditional paths for static preparation"
                    )
        return combined

    pending = deque(step_id for step_id in edges if indegree[step_id] == 0)
    while pending:
        step_id = pending.popleft()
        step = by_id[step_id]
        before: set[UUID] = set()
        required_guards: set[BranchGuard] = {frozenset()}
        # Explicit dependencies are all-of prerequisites, not alternative paths.
        for dependency in step.depends_on:
            before.update(guaranteed[dependency] | {dependency})
            required_guards = combine(required_guards, guards[dependency])
            if not required_guards:
                raise ValueError("Dependencies cannot require mutually exclusive branches")
        if controls[step_id]:
            alternative_predecessors: list[set[UUID]] = []
            possible_guards: set[BranchGuard] = set()
            for parent, outcome in controls[step_id]:
                branch_guards = combine(guards[parent], {frozenset({(parent, outcome)})})
                branch_guards = combine(required_guards, branch_guards)
                if not branch_guards:
                    continue
                alternative_predecessors.append(guaranteed[parent] | {parent})
                possible_guards.update(branch_guards)
                if len(possible_guards) > MAX_BRANCH_ALTERNATIVES:
                    raise ValueError(
                        "Too many alternative conditional paths for static preparation"
                    )
            if not alternative_predecessors:
                raise ValueError("A step cannot depend on a mutually exclusive branch")
            # A branch-activated merge may be reached through any incoming control.
            # Only facts common to every possible entry are guaranteed there.
            before.update(set.intersection(*alternative_predecessors))
            required_guards = possible_guards
        guaranteed[step_id] = before
        guards[step_id] = required_guards
        for target in edges[step_id]:
            indegree[target] -= 1
            if indegree[target] == 0:
                pending.append(target)
    for step in steps:
        sources = [
            value.source_step_id
            for value in step.parameters.values()
            if isinstance(value, PriorResultReference)
        ]
        if step.condition is not None:
            sources.append(step.condition.source_step_id)
        for source_id in sources:
            if source_id not in by_id or by_id[source_id].kind != "operation":
                raise ValueError("A result source must be an operation in this preparation")
            if source_id not in guaranteed[step.id]:
                raise ValueError(
                    "A result source must be a guaranteed earlier operation, not a branch sibling"
                )
    return guaranteed


def validate_scalar(field: OperationField, value: Scalar, *, constraints: bool = True) -> None:
    if field.type == "string" and not isinstance(value, str):
        raise ValueError("Expected a string")
    if field.type == "integer" and type(value) is not int:
        raise ValueError("Expected an integer without coercion")
    if field.type == "number" and type(value) not in {int, float}:
        raise ValueError("Expected a finite number without coercion")
    if field.type == "boolean" and type(value) is not bool:
        raise ValueError("Expected a boolean without coercion")
    if field.type == "uuid":
        if not isinstance(value, str):
            raise ValueError("Expected a UUID string")
        UUID(value)
    if field.type == "datetime":
        if not isinstance(value, str):
            raise ValueError("Expected a timezone-aware datetime string")
        aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    if isinstance(value, float) and not (-float("inf") < value < float("inf")):
        raise ValueError("Numbers must be finite")
    if not constraints:
        return
    if type(value) in {int, float}:
        if field.minimum is not None and value < field.minimum:  # type: ignore[operator]
            raise ValueError("Value is below the declared minimum")
        if field.maximum is not None and value > field.maximum:  # type: ignore[operator]
            raise ValueError("Value is above the declared maximum")
    if isinstance(value, str):
        if field.max_length is not None and len(value) > field.max_length:
            raise ValueError("Value exceeds the declared maximum length")
        if field.choices is not None and value not in field.choices:
            raise ValueError("Value is not one of the declared choices")


def validate_expected_version(value: Scalar) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value == "*"
        or any(char == '"' or not 0x21 <= ord(char) <= 0x7E for char in value)
    ):
        raise ValueError("REST expected_version must be a bare opaque version without ETag quotes")


class AssetPin(Contract):
    id: UUID
    name: Name
    media_type: str
    sha256: Digest
    size: int = Field(ge=0)


class ScenarioPin(Contract):
    scenario_id: UUID
    revision_version: int = Field(ge=1)
    content: ScenarioContent


class ConfigurationSnapshot(Contract):
    id: UUID
    workspace_id: UUID
    connection_id: UUID
    version: int = Field(ge=1)
    content: ConnectionConfiguration
    digest: Digest
    connection_kind: Literal["sql", "rest", "graph"]
    connection_name: Name
    environment_id: UUID
    environment_name: Name
    template_asset: AssetPin | None = None
    created_by: UUID
    created_at: ExplicitDatetime
    execution_authorized: Literal[False] = False

    @model_validator(mode="after")
    def complete_snapshot(self) -> Self:
        validate_configuration_kind(self.content, self.connection_kind)
        if canonical_digest(self.content.model_dump(mode="json")) != self.digest:
            raise ValueError("Configuration content digest does not match its immutable content")
        template_id = (
            self.content.notification.template_asset_id if self.content.notification else None
        )
        if (self.template_asset.id if self.template_asset else None) != template_id:
            raise ValueError("The exact notification template asset must be pinned")
        return self


class ConfigurationView(ConfigurationSnapshot):
    withdrawn_at: ExplicitDatetime | None = None
    withdrawn_by: UUID | None = None


def validate_bindings(
    draft: BoardDraft, configurations: list[ConfigurationSnapshot], scenario: ScenarioPin
) -> dict[UUID, OperationDefinition]:
    guaranteed_predecessors(draft.steps)
    configs = {config.id: config for config in configurations}
    nodes = {node.id for node in scenario.content.nodes}
    bound: dict[UUID, OperationDefinition] = {}
    for step in draft.steps:
        if step.authoring_node_id is not None and step.authoring_node_id not in nodes:
            raise ValueError("Authoring references must belong to the pinned published revision")
        if step.binding is None:
            if step.parameters:
                raise ValueError("Parameters require an explicit registered operation binding")
            continue
        config = configs.get(step.binding.configuration_id)
        if config is None:
            raise ValueError("A bound configuration is unavailable")
        operation = next(
            (
                item
                for item in config.content.catalog.operations
                if (item.key, item.version)
                == (step.binding.operation_key, step.binding.operation_version)
            ),
            None,
        )
        if operation is None:
            raise ValueError("The exact registered operation key/version is unavailable")
        fields = {field.name: field for field in operation.parameters}
        if not set(step.parameters) <= fields.keys():
            raise ValueError("Parameters must be declared by the registered operation")
        bound[step.id] = operation
    for step in draft.steps:
        operation = bound.get(step.id)
        if operation is None:
            continue
        fields = {field.name: field for field in operation.parameters}
        for name, value in step.parameters.items():
            if isinstance(value, PriorResultReference):
                source = bound.get(value.source_step_id)
                if source is None:
                    raise ValueError(
                        "Bind a source operation before referencing its declared results"
                    )
                result = next(
                    (field for field in source.results if field.name == value.field), None
                )
                if result is None:
                    raise ValueError("Result bindings must name a declared source operation output")
                if not result.required:
                    raise ValueError(
                        "Result bindings must reference a required output, not an optional output"
                    )
                if result.type != fields[name].type:
                    raise ValueError(
                        "A referenced result type must exactly match the destination parameter"
                    )
            elif value is not None:
                validate_scalar(fields[name], value)
                if isinstance(operation.invocation, RestInvocation) and name == "expected_version":
                    validate_expected_version(value)
    for step in draft.steps:
        condition = step.condition
        if condition is None:
            continue
        if condition.source_step_id not in bound:
            raise ValueError("Bind a source operation before comparing its declared result")
        result = next(
            (
                field
                for field in bound[condition.source_step_id].results
                if field.name == condition.result_field
            ),
            None,
        )
        if result is None:
            raise ValueError("The condition must reference a declared operation result")
        if not result.required:
            raise ValueError("Conditions must reference a required output, not an optional output")
        if condition.operator not in {"eq", "ne"} and result.type not in {
            "integer",
            "number",
            "datetime",
        }:
            raise ValueError("Ordered comparisons require numeric or datetime results")
        validate_scalar(result, condition.value, constraints=False)
    return bound


class PreparationManifest(Contract):
    schema_version: Literal["exercise-preparation/v1"] = "exercise-preparation/v1"
    board_id: UUID
    workspace_id: UUID
    board_version: int = Field(ge=1)
    draft: BoardDraft
    scenario: ScenarioPin
    assets: list[AssetPin]
    configurations: list[ConfigurationSnapshot]
    execution_authorized: Literal[False] = False

    @model_validator(mode="after")
    def exact_pins(self) -> Self:
        if [asset.id for asset in self.assets] != self.scenario.content.asset_ids:
            raise ValueError(
                "Assets must pin the exact ordered asset versions of the published revision"
            )
        ids = [config.id for config in self.configurations]
        referenced = {step.binding.configuration_id for step in self.draft.steps if step.binding}
        if len(ids) != len(set(ids)) or set(ids) != referenced:
            raise ValueError("Configurations must exactly match the explicit bindings")
        if any(config.workspace_id != self.workspace_id for config in self.configurations):
            raise ValueError("Configuration snapshots cannot cross workspace boundaries")
        validate_bindings(self.draft, self.configurations, self.scenario)
        return self


class PreparationFinding(Contract):
    code: str
    severity: Literal["blocker", "warning", "info"]
    message: str
    path: str = ""
    step_id: UUID | None = None


def preparation_findings(manifest: PreparationManifest) -> list[PreparationFinding]:
    findings: list[PreparationFinding] = []

    def add(
        code: str,
        message: str,
        path: str = "",
        step_id: UUID | None = None,
        severity: Literal["blocker", "warning", "info"] = "blocker",
    ) -> None:
        findings.append(
            PreparationFinding(
                code=code, severity=severity, message=message, path=path, step_id=step_id
            )
        )

    draft = manifest.draft
    if not draft.steps:
        add("missing_steps", "No proposed operations have been supplied.", "/draft/steps")
    if draft.window is None:
        add(
            "missing_window",
            "Supply an explicit timezone-aware preparation window.",
            "/draft/window",
        )
    elif (
        sum(step.wait_seconds or 0 for step in draft.steps)
        > (draft.window.ends_at - draft.window.starts_at).total_seconds()
    ):
        add(
            "waits_exceed_window",
            "The bounded waits exceed the supplied time window.",
            "/draft/window",
        )
    if not draft.recovery.strip():
        add(
            "missing_recovery",
            "Supply a recovery decision or an explicit read-only rationale.",
            "/draft/recovery",
        )
    operations = validate_bindings(draft, manifest.configurations, manifest.scenario)
    notifications = 0
    for step in draft.steps:
        path = f"/draft/steps/{step.id}"
        if step.kind == "operation" and step.binding is None:
            add(
                "missing_binding",
                "Select a registered configuration and exact operation version.",
                path,
                step.id,
            )
        operation = operations.get(step.id)
        if operation is not None:
            notifications += operation.effect == "notify"
            for field in operation.parameters:
                value = step.parameters.get(field.name)
                if field.required and value is None:
                    add(
                        "missing_parameter",
                        f"Required parameter {field.name} is unresolved.",
                        f"{path}/parameters/{field.name}",
                        step.id,
                    )
                elif isinstance(value, PriorResultReference):
                    add(
                        "result_binding_unverified",
                        "This value refers to a declared earlier result; no operation has run or returned it.",
                        f"{path}/parameters/{field.name}",
                        step.id,
                        severity="warning",
                    )
    if draft.notification_budget < notifications:
        add(
            "notification_budget_exceeded",
            "The notification budget is smaller than the proposed notification count.",
            "/draft/notification_budget",
        )
    for config in manifest.configurations:
        path = f"/configurations/{config.id}"
        content = config.content
        if (
            content.classification != "nonproduction"
            or config.environment_name.strip().casefold() == "production"
        ):
            add(
                "target_classification_ineligible",
                "Production or unclassified targets are ineligible for execution.",
                path,
            )
        required = ["resource_id", "endpoint", "identity_ref"]
        if config.connection_kind == "sql":
            required.append("database")
        for target_field in required:
            if not getattr(content, target_field).strip():
                add(
                    f"missing_target_{target_field}",
                    f"Target {target_field} is unresolved.",
                    f"{path}/content/{target_field}",
                )
        if config.connection_kind == "graph":
            notification = content.notification
            for routing_field in ("template_asset_id", "sender", "recipients", "trusted_link"):
                if notification is None or not getattr(notification, routing_field):
                    add(
                        f"missing_notification_{routing_field}",
                        f"Notification {routing_field} is unresolved.",
                        f"{path}/content/notification/{routing_field}",
                    )
            add(
                "delivery_unverified",
                "No message has been sent and delivery has not been observed.",
                path,
                severity="warning",
            )
        add(
            "target_contents_unverified",
            "Target contents and record identifiers have not been checked against a live system.",
            path,
            severity="warning",
        )
        add(
            "connectivity_unverified",
            "Target connectivity has not been verified; registration makes no network requests.",
            path,
            severity="warning",
        )
        add(
            "permissions_unverified",
            "Effective target permissions have not been verified.",
            path,
            severity="warning",
        )
    add(
        "live_readiness_unverified",
        "This static preview is not evidence of live readiness or execution.",
        severity="warning",
    )
    add(
        "execution_disabled",
        "Execution is disabled. Preparation approval cannot authorize execution.",
    )
    return findings


class BoardCreate(Contract):
    name: Name
    scenario_id: UUID
    revision_version: Annotated[StrictInt, Field(ge=1)]


class ApproverGrantInput(Contract):
    object_id: UUID


class ApproverGrantView(Contract):
    id: UUID
    workspace_id: UUID
    object_id: UUID
    granted_by: UUID
    granted_at: ExplicitDatetime


class PreparationPreviewView(Contract):
    id: UUID
    board_id: UUID
    board_version: int
    sequence: int
    digest: Digest
    manifest: PreparationManifest
    findings: list[PreparationFinding]
    created_by: UUID
    created_at: ExplicitDatetime
    is_current: bool
    execution_authorized: Literal[False] = False
    execution_eligible: Literal[False] = False


class PreparationApprovalInput(Contract):
    preview_id: UUID
    digest: Digest
    decision: Literal["approved", "rejected"]
    expires_at: ExplicitDatetime
    acknowledge_unverified: Literal[True]
    note: str = Field(default="", max_length=2000)

    @field_validator("acknowledge_unverified", mode="before")
    @classmethod
    def explicit_acknowledgement(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicitly acknowledge unresolved live prerequisites")
        return value

    @field_validator("expires_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return aware_utc(value)


class ApprovalValidity(Contract):
    valid: bool
    reasons: list[str]


class PreparationApprovalView(Contract):
    id: UUID
    board_id: UUID
    board_version: int
    preview_id: UUID
    digest: Digest
    sequence: int
    kind: Literal["preparation"] = "preparation"
    execution_authorized: Literal[False] = False
    reviewer: UUID
    decision: Literal["approved", "rejected"]
    acknowledge_unverified: Literal[True] = True
    expires_at: ExplicitDatetime
    note: str
    created_at: ExplicitDatetime
    validity: ApprovalValidity
    revoked_at: ExplicitDatetime | None = None
    revoked_by: UUID | None = None


class BoardSummary(Contract):
    id: UUID
    workspace_id: UUID
    name: Name
    version: int
    scenario_id: UUID
    revision_version: int
    created_by: UUID
    created_at: ExplicitDatetime
    updated_at: ExplicitDatetime
    preparation_status: Literal["draft", "previewed"]
    approval_status: Literal["none", "approved", "rejected", "invalid"]
    execution_authorized: Literal[False] = False


class BoardView(BoardSummary):
    draft: BoardDraft
    scenario: ScenarioPin
    assets: list[AssetPin]
    contributors: list[UUID]
    latest_preview: PreparationPreviewView | None
    current_approval: PreparationApprovalView | None
    can_edit: bool
    can_approve: bool
    approval_blockers: list[str]


class ExecutionDisabled(Contract):
    code: Literal["execution_disabled"] = "execution_disabled"
    message: str = "Execution is disabled. Preparation approval is not authorization to execute."
    execution_authorized: Literal[False] = False


class ExecutionAuthorization(Contract):
    kind: Literal["execution"]
    execution_authorized: Literal[True]
    approval_id: UUID
    reviewer: UUID
    manifest_digest: Digest
    issued_at: ExplicitDatetime
    expires_at: ExplicitDatetime

    @field_validator("execution_authorized", mode="before")
    @classmethod
    def explicit_authority(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Explicit future execution authorization is required")
        return value

    @model_validator(mode="after")
    def fresh_authorization(self) -> Self:
        current = datetime.now(UTC)
        if not self.issued_at <= current < self.expires_at:
            raise ValueError("A fresh, unexpired execution authorization is required")
        return self


class LiveReadinessAttestation(Contract):
    configuration_id: UUID
    checked_at: ExplicitDatetime
    expires_at: ExplicitDatetime
    evidence_ids: list[UUID] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def fresh_evidence(self) -> Self:
        if not self.checked_at <= datetime.now(UTC) < self.expires_at:
            raise ValueError("Unexpired live readiness evidence is required")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Live evidence references must be unique")
        return self


class ExecutionManifest(Contract):
    """Future validation boundary only; no API accepts or dispatches this type."""

    schema_version: Literal["exercise-execution/v1"]
    preparation: PreparationManifest
    authorization: ExecutionAuthorization
    live_readiness: list[LiveReadinessAttestation] = Field(min_length=1, max_length=MAX_STEPS)

    @model_validator(mode="after")
    def reject_unresolved_preparation(self) -> Self:
        preparation = self.preparation
        if self.authorization.manifest_digest != canonical_digest(
            preparation.model_dump(mode="json")
        ):
            raise ValueError("Execution authorization must pin the exact complete manifest")
        blockers = [
            finding
            for finding in preparation_findings(preparation)
            if finding.severity == "blocker" and finding.code != "execution_disabled"
        ]
        if blockers or any(
            value is None for step in preparation.draft.steps for value in step.parameters.values()
        ):
            raise ValueError("Unresolved preparation cannot be used as an execution manifest")
        configs = {config.id for config in preparation.configurations}
        attestations = {item.configuration_id for item in self.live_readiness}
        if not configs or configs != attestations or len(attestations) != len(self.live_readiness):
            raise ValueError("Every exact target configuration requires live readiness evidence")
        window = preparation.draft.window
        if window is None or window.starts_at <= datetime.now(UTC):
            raise ValueError("Execution requires a complete future bounded window")
        if self.authorization.expires_at < window.ends_at or any(
            item.expires_at < window.ends_at for item in self.live_readiness
        ):
            raise ValueError("Execution authorization and readiness must cover the entire window")
        return self
