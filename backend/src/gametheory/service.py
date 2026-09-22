from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from gametheory.auth import Principal, authorize
from gametheory.domain import ScenarioContent, ScenarioView
from gametheory.persistence import (
    Asset,
    Audit,
    Connection,
    ConnectionGrant,
    Environment,
    Scenario,
    now,
    timestamp,
)


def audit(
    db: Session,
    actor: Principal,
    operation: str,
    resource_id: str,
    workspace_id: str | None = None,
    version: int | None = None,
    correlation: str | None = None,
) -> None:
    db.add(
        Audit(
            organization_id=actor.tenant,
            actor=actor.object_id,
            operation=operation,
            resource_id=resource_id,
            workspace_id=workspace_id,
            version=version,
            correlation_id=correlation or str(uuid4()),
        )
    )


def get_scenario(
    db: Session,
    actor: Principal,
    workspace_id: str,
    scenario_id: str,
    role: str = "viewer",
) -> Scenario:
    authorize(db, actor, workspace_id, role)
    scenario = db.get(Scenario, scenario_id)
    if scenario is None or scenario.workspace_id != workspace_id:
        raise HTTPException(404, "Scenario not found")
    return scenario


def scenario_view(scenario: Scenario) -> ScenarioView:
    return ScenarioView(
        id=scenario.id,
        workspace_id=scenario.workspace_id,
        version=scenario.version,
        content=ScenarioContent.model_validate_json(scenario.content),
        updated_at=timestamp(scenario.updated_at),
    )


def expected_version(value: str | None, resource: str = "draft") -> int:
    if value is None:
        raise HTTPException(428, f"Send the current {resource} ETag in If-Match")
    if not value.startswith('"') or not value.endswith('"') or not value[1:-1].isdigit():
        raise HTTPException(400, "If-Match must contain one strong numeric ETag")
    version = int(value[1:-1])
    if version < 1:
        raise HTTPException(400, "Invalid draft version")
    return version


def connection_available(
    db: Session, connection: Connection, workspace_id: str, *, fence: bool = False
) -> bool:
    if connection.scope == "organization":
        return True
    if connection.scope == "workspace":
        return connection.workspace_id == workspace_id
    if fence:
        return (
            db.scalar(
                select(ConnectionGrant)
                .where(
                    ConnectionGrant.connection_id == connection.id,
                    ConnectionGrant.workspace_id == workspace_id,
                )
                .with_hint(ConnectionGrant, "WITH (HOLDLOCK)", dialect_name="mssql")
                .execution_options(populate_existing=True)
            )
            is not None
        )
    return db.get(ConnectionGrant, (connection.id, workspace_id)) is not None


def validate_references(
    db: Session,
    actor: Principal,
    workspace_id: str,
    content: ScenarioContent,
) -> None:
    for asset_id in content.asset_ids:
        asset = db.get(Asset, str(asset_id))
        if asset is None or asset.workspace_id != workspace_id or asset.state != "ready":
            raise HTTPException(422, f"Asset {asset_id} is unavailable in this workspace")
    for node in content.nodes:
        if node.environment_id:
            environment = db.get(Environment, str(node.environment_id))
            if environment is None or environment.organization_id != actor.tenant:
                raise HTTPException(422, f"Node {node.id} has an unavailable environment")
        if node.connection_id:
            connection = db.get(Connection, str(node.connection_id))
            if (
                connection is None
                or connection.organization_id != actor.tenant
                or not connection_available(db, connection, workspace_id)
            ):
                raise HTTPException(422, f"Node {node.id} has an unavailable connection")
            if str(node.environment_id) != connection.environment_id:
                raise HTTPException(422, f"Node {node.id} must use its connection's environment")


def save_scenario(
    db: Session,
    actor: Principal,
    scenario: Scenario,
    content: ScenarioContent,
    version: int,
    correlation: str,
) -> ScenarioView:
    validate_references(db, actor, scenario.workspace_id, content)
    changed = db.execute(
        update(Scenario)
        .where(Scenario.id == scenario.id, Scenario.version == version)
        .values(content=content.model_dump_json(), version=version + 1, updated_at=now())
        .returning(Scenario.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    db.expire(scenario)
    if changed is None:
        raise HTTPException(
            409,
            {
                "message": "Draft changed. Your unsaved work has not been overwritten.",
                "current": scenario_view(scenario).model_dump(mode="json"),
            },
        )
    audit(db, actor, "scenario.saved", scenario.id, scenario.workspace_id, version + 1, correlation)
    return scenario_view(scenario)


def list_connections(db: Session, actor: Principal, workspace_id: str) -> list[Connection]:
    rows = db.scalars(select(Connection).where(Connection.organization_id == actor.tenant)).all()
    return [row for row in rows if connection_available(db, row, workspace_id)]
