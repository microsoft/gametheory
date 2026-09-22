import hashlib
import re
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote, urlparse
from uuid import UUID

import httpx
from azure.core.exceptions import AzureError
from azure.identity import ManagedIdentityCredential
from fastapi import HTTPException
from pydantic import Field, TypeAdapter, ValidationError
from sqlalchemy import URL, create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from gametheory.config import get_settings
from gametheory.domain import Contract
from gametheory.execution import Outcome
from gametheory.preparation import (
    ConfigurationSnapshot,
    Digest,
    OperationDefinition,
    RestInvocation,
    Scalar,
    SqlInvocation,
    canonical_digest,
    safe_endpoint,
    strict_json,
    validate_expected_version,
    validate_scalar,
)


class AllowedOperation(Contract):
    digest: Digest
    safe_replay: bool = False


class TargetBinding(Contract):
    configuration_id: UUID
    configuration_digest: Digest
    environment_id: UUID
    classification: Literal["nonproduction", "production"]
    resource_id: str = Field(min_length=1, max_length=512)
    endpoint: str = Field(min_length=1, max_length=1000)
    database: str = Field(default="", max_length=128)
    identity_ref: str = Field(min_length=1, max_length=512)
    client_id: UUID
    token_scope: str = Field(default="", max_length=512)
    operations: list[AllowedOperation] = Field(min_length=1, max_length=200)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_response_bytes: int = Field(default=65536, ge=1024, le=65536)

    @property
    def digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))

    def operation(self, operation: OperationDefinition) -> AllowedOperation:
        digest = canonical_digest(operation.model_dump(mode="json"))
        match = next((item for item in self.operations if item.digest == digest), None)
        if match is None:
            raise HTTPException(409, "Operation is not authorized by the operator target binding")
        if (
            match.safe_replay
            and operation.effect == "write"
            and operation.invocation.kind == "sql"
            and not any(field.name == "idempotency_key" for field in operation.parameters)
        ):
            raise HTTPException(
                409, "Safe SQL replay requires a dispatcher-owned idempotency parameter"
            )
        return match


class AdapterResult(Contract):
    outcome: Outcome
    values: dict[str, Scalar | None] = Field(default_factory=dict)
    reason: str | None = None


class QueryTimeoutConnection(Protocol):
    timeout: int


def bindings() -> dict[UUID, TargetBinding]:
    path = get_settings().execution_bindings_file
    if not path:
        raise HTTPException(503, "Operator target bindings are not configured")
    try:
        values = TypeAdapter(list[TargetBinding]).validate_python(
            strict_json(Path(path).read_bytes())
        )
    except (OSError, ValueError, ValidationError) as exc:
        raise HTTPException(503, "Operator target bindings are unavailable or invalid") from exc
    if len({item.configuration_id for item in values}) != len(values):
        raise HTTPException(503, "Operator bindings contain duplicate configurations")
    return {item.configuration_id: item for item in values}


def binding_for(
    config: ConfigurationSnapshot, available: dict[UUID, TargetBinding]
) -> TargetBinding:
    binding = available.get(config.id)
    if binding is None:
        raise HTTPException(409, "No operator-approved binding for this exact configuration")
    content = config.content
    if (
        binding.configuration_digest != config.digest
        or binding.environment_id != config.environment_id
        or binding.classification != content.classification
        or any(
            getattr(binding, name) != getattr(content, name)
            for name in ("resource_id", "endpoint", "database", "identity_ref")
        )
    ):
        raise HTTPException(409, "Target or identity differs from its approved configuration")
    if config.connection_kind == "sql" and get_settings().sql_url:
        application = make_url(get_settings().sql_url)
        if (application.host or "").casefold() == binding.endpoint.casefold() and (
            application.database or ""
        ).casefold() == binding.database.casefold():
            raise HTTPException(409, "The application database cannot be an exercise target")
    if config.connection_kind == "rest":
        safe_endpoint(binding.endpoint)
        if urlparse(binding.endpoint).scheme != "https":
            raise HTTPException(409, "Authenticated execution targets must use HTTPS")
        if urlparse(binding.endpoint).hostname in {
            "graph.microsoft.com",
            "graph.microsoft.us",
            "dod-graph.microsoft.us",
            "microsoftgraph.chinacloudapi.cn",
        }:
            raise HTTPException(
                409, "Microsoft Graph requires the separately implemented notification gate"
            )
        if not binding.token_scope.startswith("api://") and not binding.token_scope.startswith(
            "https://"
        ):
            raise HTTPException(409, "REST execution requires an explicit approved token audience")
        scope = urlparse(binding.token_scope)
        if scope.username or scope.password or scope.query or scope.fragment or not scope.netloc:
            raise HTTPException(409, "Token audience must be a non-secret resource scope")
    return binding


def operation_key(run_id: str, phase: str, step_id: str) -> str:
    return hashlib.sha256(f"exercise/v1:{run_id}:{phase}:{step_id}".encode()).hexdigest()


def output(operation: OperationDefinition, raw: dict[str, object]) -> dict[str, Scalar | None]:
    result: dict[str, Scalar | None] = {}
    for field in operation.results:
        value = raw.get(field.name)
        if value is None:
            if field.required:
                raise ValueError(f"Missing required result: {field.name}")
            result[field.name] = None
            continue
        if isinstance(value, UUID):
            value = str(value)
        elif isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("Source datetime must include its UTC offset")
            value = value.astimezone(UTC).isoformat()
        elif isinstance(value, Decimal):
            value = float(value)
        scalar: Scalar = TypeAdapter(Scalar).validate_python(value, strict=True)
        validate_scalar(field, scalar)
        result[field.name] = scalar
    return result


def rest_request(
    operation: OperationDefinition,
    parameters: dict[str, Scalar],
    key: str,
) -> tuple[str, dict[str, str], dict[str, Scalar]]:
    invocation = operation.invocation
    if not isinstance(invocation, RestInvocation):
        raise ValueError("REST invocation required")
    remaining = dict(parameters)
    path = invocation.path
    for name in re.findall(r"\{([^}]+)\}", path):
        value = str(remaining.pop(name))
        if value in {".", ".."} or "/" in value or "\\" in value or "%" in value:
            raise ValueError("Path values cannot alter route structure")
        path = path.replace("{" + name + "}", quote(value, safe=""))
    headers = {}
    version = remaining.pop("expected_version", None)
    if version is not None:
        validate_expected_version(version)
        headers["If-Match"] = f'"{version}"'
    if operation.effect == "write":
        headers["Idempotency-Key"] = key
    return path, headers, remaining


def invoke(
    binding: TargetBinding,
    operation: OperationDefinition,
    parameters: dict[str, Scalar],
    key: str,
) -> AdapterResult:
    binding.operation(operation)
    uncertain: Outcome = "unknown" if operation.effect == "write" else "failed"
    try:
        if isinstance(operation.invocation, SqlInvocation):
            raw = sql_call(binding, operation, parameters, key)
        elif isinstance(operation.invocation, RestInvocation):
            rest_result = rest_call(binding, operation, parameters, key)
            if isinstance(rest_result, AdapterResult):
                return rest_result
            raw = rest_result
        else:
            return AdapterResult(outcome="rejected", reason="Unsupported transport")
        outcome = raw.get("outcome", "succeeded")
        if outcome in {"rejected", "failed", "unknown"}:
            return AdapterResult(
                outcome=TypeAdapter(Outcome).validate_python(outcome),
                reason="Target reported a non-successful outcome; inspect correlated target evidence",
            )
        if outcome != "succeeded":
            raise ValueError("Invalid target outcome")
        values = output(operation, raw)
        result = AdapterResult(outcome="succeeded", values=values)
        if len(result.model_dump_json().encode()) > binding.max_response_bytes:
            raise ValueError("Validated result exceeds the approved response bound")
        return result
    except (SQLAlchemyError, httpx.HTTPError, AzureError, TimeoutError, ValueError) as exc:
        return AdapterResult(
            outcome=uncertain,
            reason=f"Target call did not produce confirmed evidence ({type(exc).__name__}); reconcile before retrying",
        )


def sql_call(
    binding: TargetBinding, operation: OperationDefinition, parameters: dict[str, Scalar], key: str
) -> dict[str, object]:
    if not isinstance(operation.invocation, SqlInvocation):
        raise ValueError("SQL invocation required")
    params = dict(parameters)
    if any(field.name == "idempotency_key" for field in operation.parameters):
        params["idempotency_key"] = key
    # Identifiers are validated catalog members; only values are bound parameters.
    statement = f"EXEC {operation.invocation.procedure} " + ", ".join(
        f"@{name}=:{name}" for name in params
    )
    url = URL.create(
        "mssql+pyodbc",
        host=binding.endpoint,
        database=binding.database,
        query={
            "driver": "ODBC Driver 18 for SQL Server",
            "Encrypt": "yes",
            "TrustServerCertificate": "no",
            "authentication": "ActiveDirectoryMsi",
            "UID": str(binding.client_id),
        },
    )
    engine = create_engine(url, connect_args={"timeout": binding.timeout_seconds})

    @event.listens_for(engine, "connect")
    def timeout(connection: QueryTimeoutConnection, _record: object) -> None:
        connection.timeout = binding.timeout_seconds

    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            if connection.scalar(text("SELECT DB_NAME()")) != binding.database:
                raise ValueError("Target database mismatch")
            return dict(connection.execute(text(statement), params).mappings().one())
    finally:
        engine.dispose()


def rest_call(
    binding: TargetBinding,
    operation: OperationDefinition,
    parameters: dict[str, Scalar],
    key: str,
) -> dict[str, object] | AdapterResult:
    if not isinstance(operation.invocation, RestInvocation):
        raise ValueError("REST invocation required")
    path, headers, remaining = rest_request(operation, parameters, key)
    deadline = time.monotonic() + binding.timeout_seconds
    with ManagedIdentityCredential(
        client_id=str(binding.client_id),
        connection_timeout=binding.timeout_seconds,
        read_timeout=binding.timeout_seconds,
        retry_total=0,
    ) as credential:
        headers["Authorization"] = f"Bearer {credential.get_token(binding.token_scope).token}"
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise TimeoutError("Identity acquisition exhausted the operation deadline")
        with httpx.Client(
            timeout=remaining_seconds, follow_redirects=False, trust_env=False
        ) as client:
            with client.stream(
                operation.invocation.method,
                binding.endpoint.rstrip("/") + path,
                headers=headers,
                params=remaining if operation.invocation.method == "GET" else None,
                json=remaining if operation.invocation.method != "GET" else None,
            ) as response:
                if response.status_code == 202 and operation.effect == "write":
                    return AdapterResult(
                        outcome="unknown",
                        reason="Provider acceptance is not a confirmed completed effect; reconciliation is required",
                    )
                if not 200 <= response.status_code < 300:
                    status = response.status_code
                    outcome: Outcome = (
                        "rejected"
                        if status in {400, 401, 403, 404, 409, 412, 422, 428}
                        else ("unknown" if operation.effect == "write" else "failed")
                    )
                    return AdapterResult(
                        outcome=outcome,
                        reason=f"Target returned HTTP {status}; redirects are not followed",
                    )
                data = bytearray()
                for chunk in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise TimeoutError("Target response exceeded the operation deadline")
                    data.extend(chunk)
                    if len(data) > binding.max_response_bytes:
                        raise ValueError("Target response exceeds the approved bound")
                parsed = strict_json(bytes(data))
                if not isinstance(parsed, dict):
                    raise ValueError("Target response must be one result object")
                return parsed
