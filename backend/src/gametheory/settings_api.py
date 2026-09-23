from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from gametheory.auth import require_admin
from gametheory.config import get_settings
from gametheory.domain import Contract
from gametheory.environment_policy import (
    EnvironmentPolicyInput,
    EnvironmentPolicyView,
    latest_policy,
    policy_environment,
    policy_view,
    save_policy,
)
from gametheory.persistence import Environment, EnvironmentPolicyRecord
from gametheory.preparation_api import DB, RESOURCE_HEADERS, Actor, Match, restricted_json
from gametheory.service import expected_version

router = APIRouter(
    prefix="/api/admin", tags=["Administration"], dependencies=[Depends(restricted_json)]
)


class RuntimeStatus(Contract):
    execution_enabled: bool
    sql_configured: bool
    scheduler_configured: bool
    target_bindings_configured: bool
    run_assistant_enabled: bool
    message: str


@router.get("/runtime", response_model=RuntimeStatus)
def runtime(db: DB, actor: Actor) -> RuntimeStatus:
    require_admin(db, actor)
    settings = get_settings()
    return RuntimeStatus(
        execution_enabled=settings.execution_enabled,
        sql_configured=bool(settings.sql_url),
        scheduler_configured=bool(settings.scheduler_endpoint and settings.execution_taskhub),
        target_bindings_configured=bool(settings.execution_bindings_file),
        run_assistant_enabled=settings.run_assistant_enabled,
        message="Configuration is not live readiness. Environment policy never grants target access.",
    )


@router.get("/environment-policies", response_model=list[EnvironmentPolicyView])
def policies(db: DB, actor: Actor) -> list[EnvironmentPolicyView]:
    require_admin(db, actor)
    return [
        policy_view(environment, latest_policy(db, environment.id))
        for environment in db.scalars(
            select(Environment)
            .where(Environment.organization_id == actor.tenant)
            .order_by(Environment.name, Environment.id)
        )
    ]


@router.get(
    "/environment-policies/{eid}",
    response_model=EnvironmentPolicyView,
    responses={200: {"headers": RESOURCE_HEADERS}},
)
def policy(eid: UUID, db: DB, actor: Actor, response: Response) -> EnvironmentPolicyView:
    environment = policy_environment(db, actor, str(eid))
    record = latest_policy(db, str(eid))
    response.headers["ETag"] = f'"{record.version}"'
    return policy_view(environment, record)


@router.put(
    "/environment-policies/{eid}",
    response_model=EnvironmentPolicyView,
    responses={200: {"headers": RESOURCE_HEADERS}},
)
def update_policy(
    eid: UUID,
    body: EnvironmentPolicyInput,
    db: DB,
    actor: Actor,
    request: Request,
    response: Response,
    if_match: Match = None,
) -> EnvironmentPolicyView:
    result = save_policy(
        db,
        actor,
        str(eid),
        body,
        expected_version(if_match, "environment policy"),
        request.state.correlation,
    )
    response.headers["ETag"] = f'"{result.version}"'
    return result


@router.get("/environment-policies/{eid}/history", response_model=list[EnvironmentPolicyView])
def policy_history(eid: UUID, db: DB, actor: Actor) -> list[EnvironmentPolicyView]:
    environment = policy_environment(db, actor, str(eid))
    return [
        policy_view(environment, record)
        for record in db.scalars(
            select(EnvironmentPolicyRecord)
            .where(EnvironmentPolicyRecord.environment_id == str(eid))
            .order_by(EnvironmentPolicyRecord.version.desc())
        )
    ]
