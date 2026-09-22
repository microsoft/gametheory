from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

API_PERMISSIONS = {
    "organizations": "SELECT",
    "administrators": "SELECT",
    "workspaces": "SELECT, INSERT",
    "memberships": "SELECT, INSERT, UPDATE, DELETE",
    "environments": "SELECT, INSERT",
    "environment_policies": "SELECT, INSERT",
    "target_readiness": "SELECT",
    "exercise_run_states": "SELECT, INSERT, UPDATE",
    "run_steps": "SELECT, INSERT, UPDATE",
    "run_dispatches": "SELECT, INSERT, UPDATE",
    **{
        table: "SELECT, INSERT"
        for table in (
            "execution_grants",
            "execution_grant_revocations",
            "exercise_runs",
            "run_authorizations",
            "run_approvals",
            "run_approval_revocations",
            "run_events",
        )
    },
    "connections": "SELECT, INSERT",
    "connection_grants": "SELECT, INSERT",
    "scenarios": "SELECT, INSERT, UPDATE",
    "revisions": "SELECT, INSERT",
    "comments": "SELECT, INSERT",
    "assets": "SELECT, INSERT, UPDATE",
    "planning_requests": "SELECT, INSERT, UPDATE",
    "dispatch_intents": "SELECT, INSERT",
    "audit": "SELECT, INSERT",
    "boards": "SELECT, INSERT, UPDATE",
    **{
        table: "SELECT, INSERT"
        for table in (
            "connection_configurations",
            "configuration_withdrawals",
            "workspace_approver_grants",
            "approver_grant_revocations",
            "board_origins",
            "board_contributors",
            "board_previews",
            "board_approvals",
            "approval_revocations",
        )
    },
}
WORKER_PERMISSIONS = {
    **{
        table: "SELECT"
        for table in (
            "organizations",
            "administrators",
            "workspaces",
            "memberships",
            "environments",
            "connections",
            "connection_grants",
            "scenarios",
            "assets",
        )
    },
    "planning_requests": "SELECT, UPDATE",
    "dispatch_intents": "SELECT, UPDATE",
    "audit": "INSERT",
}

EXECUTOR_PERMISSIONS = {
    **{
        table: "SELECT"
        for table in (
            "organizations",
            "administrators",
            "workspaces",
            "memberships",
            "environments",
            "environment_policies",
            "connections",
            "connection_grants",
            "connection_configurations",
            "configuration_withdrawals",
            "assets",
            "board_origins",
            "board_contributors",
            "execution_grants",
            "execution_grant_revocations",
            "exercise_runs",
            "run_authorizations",
            "run_approvals",
            "run_approval_revocations",
            "target_readiness",
        )
    },
    "exercise_run_states": "SELECT, UPDATE",
    "run_steps": "SELECT, UPDATE",
    "run_dispatches": "SELECT, UPDATE",
    "run_events": "SELECT, INSERT",
    "audit": "INSERT",
}


def provision_runtime_users(
    db: Session,
    api_client_id: UUID,
    worker_client_id: UUID,
    executor_client_id: UUID | None = None,
) -> None:
    identities = [api_client_id, worker_client_id] + (
        [executor_client_id] if executor_client_id else []
    )
    if len(set(identities)) != len(identities):
        raise ValueError("API, planning worker, and executor must have separate identities")
    users = [
        ("gametheory_api", api_client_id, API_PERMISSIONS),
        ("gametheory_worker", worker_client_id, WORKER_PERMISSIONS),
    ]
    if executor_client_id:
        users.append(("gametheory_executor", executor_client_id, EXECUTOR_PERMISSIONS))
    for name, client_id, permissions in users:
        existing = db.execute(
            text("SELECT sid, type FROM sys.database_principals WHERE name = :name"),
            {"name": name},
        ).first()
        # Azure SQL uses the application's client ID, in SQL GUID byte order.
        sid = client_id.bytes_le
        if existing is not None:
            if existing[0] != sid or existing[1] != "E":
                raise ValueError(f"Existing SQL principal {name} belongs to a different identity")
        else:
            db.execute(text(f"CREATE USER [{name}] WITH SID = 0x{sid.hex()}, TYPE = E"))
        for table, grants in permissions.items():
            db.execute(text(f"GRANT {grants} ON OBJECT::[dbo].[{table}] TO [{name}]"))
