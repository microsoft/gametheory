from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

API_PERMISSIONS = {
    "organizations": "SELECT",
    "administrators": "SELECT",
    "workspaces": "SELECT, INSERT",
    "memberships": "SELECT, INSERT, UPDATE, DELETE",
    "environments": "SELECT, INSERT",
    "connections": "SELECT, INSERT",
    "connection_grants": "SELECT, INSERT",
    "scenarios": "SELECT, INSERT, UPDATE",
    "revisions": "SELECT, INSERT",
    "comments": "SELECT, INSERT",
    "assets": "SELECT, INSERT, UPDATE",
    "planning_requests": "SELECT, INSERT, UPDATE",
    "dispatch_intents": "SELECT, INSERT",
    "audit": "SELECT, INSERT",
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


def provision_runtime_users(db: Session, api_client_id: UUID, worker_client_id: UUID) -> None:
    if api_client_id == worker_client_id:
        raise ValueError("API and worker must have separate identities")
    for name, client_id, permissions in (
        ("gametheory_api", api_client_id, API_PERMISSIONS),
        ("gametheory_worker", worker_client_id, WORKER_PERMISSIONS),
    ):
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
