import argparse
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from azure.core.exceptions import ResourceNotFoundError
from sqlalchemy import select, text, update

from gametheory.assets import blob_service
from gametheory.auth import Principal
from gametheory.config import get_settings
from gametheory.persistence import (
    Administrator,
    Asset,
    Environment,
    Organization,
    Workspace,
    now,
    session_factory,
)
from gametheory.service import audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Game Theory operator commands")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap", help="Explicitly grant an initial tenant administrator"
    )
    bootstrap.add_argument("--tenant", required=True, type=UUID)
    bootstrap.add_argument("--object-id", required=True, type=UUID)
    bootstrap.add_argument("--organization-name", required=True)
    schema = commands.add_parser("openapi", help="Export frontend API contract")
    schema.add_argument("--output", default="backend/openapi.json")
    cleanup = commands.add_parser(
        "cleanup-staged", help="Inspect or retire application-owned abandoned uploads"
    )
    cleanup.add_argument("--apply", action="store_true")
    cleanup.add_argument("--older-than-hours", type=int, default=24)
    grants = commands.add_parser(
        "database-grants", help="Create contained managed-identity users and table-scoped grants"
    )
    grants.add_argument("--api-client-id", required=True, type=UUID)
    grants.add_argument("--worker-client-id", required=True, type=UUID)
    args = parser.parse_args()
    if args.command == "database-grants":
        from gametheory.database_setup import provision_runtime_users

        with session_factory().begin() as db:
            provision_runtime_users(db, args.api_client_id, args.worker_client_id)
        print("Runtime users granted table-scoped permissions; no DDL or administrator writes.")
        return
    if args.command == "openapi":
        from gametheory.api import app

        Path(args.output).write_text(json.dumps(app.openapi(), indent=2) + "\n")
        return
    if args.command == "cleanup-staged":
        if args.older_than_hours < 1:
            parser.error("The minimum staging grace period is one hour")
        cutoff = now() - timedelta(hours=args.older_than_hours)
        with session_factory().begin() as db:
            ids = list(
                db.scalars(
                    select(Asset.id).where(
                        Asset.state.in_(["staged", "cleanup_pending"]),
                        Asset.created_at < cutoff,
                    )
                )
            )
        for aid in ids:
            print(f"Staged asset: {aid}")
            if not args.apply:
                continue
            with session_factory().begin() as db:
                asset = db.get(Asset, aid)
                if asset is None or asset.blob_key != f"{asset.workspace_id}/{asset.id}":
                    raise RuntimeError("Refusing cleanup of an unrecognized Blob key")
                workspace = db.get(Workspace, asset.workspace_id)
                if workspace is None:
                    raise RuntimeError("Staged asset workspace is unavailable")
                sql_principal = db.scalar(text("SELECT ORIGINAL_LOGIN()"))
                if not isinstance(sql_principal, str) or not sql_principal:
                    raise RuntimeError("Cannot identify the cleanup SQL principal")
                operator = Principal(workspace.organization_id, f"sql:{sql_principal}")
                workspace_id = workspace.id
                key = db.execute(
                    update(Asset)
                    .where(
                        Asset.id == aid,
                        Asset.state.in_(["staged", "cleanup_pending"]),
                        Asset.created_at < cutoff,
                    )
                    .values(state="cleanup_pending")
                    .returning(Asset.blob_key)
                ).scalar_one_or_none()
                if key:
                    audit(db, operator, "asset.cleanup_fenced", aid, workspace_id)
            if key is None:
                continue
            try:
                blob_service().get_blob_client(get_settings().blob_container, key).delete_blob()
            except ResourceNotFoundError:
                print(f"No Blob exists for staged asset {aid}; finalizing retirement")
            with session_factory().begin() as db:
                db.execute(
                    update(Asset)
                    .where(
                        Asset.id == aid,
                        Asset.state == "cleanup_pending",
                    )
                    .values(state="abandoned")
                )
                audit(db, operator, "asset.retired", aid, workspace_id)
        print(
            "Cleanup complete."
            if args.apply
            else "Inspection only; use --apply to retire these staged uploads."
        )
        return
    settings = get_settings()
    if str(args.tenant) != settings.tenant_id:
        parser.error("Tenant must match configured GT_TENANT_ID")
    if not 1 <= len(args.organization_name.strip()) <= 160:
        parser.error("Organization name must have 1-160 characters")
    actor = Principal(str(args.tenant), str(args.object_id))
    with session_factory().begin() as db:
        organization = db.get(Organization, actor.tenant)
        if organization is None:
            db.add(Organization(id=actor.tenant, name=args.organization_name.strip()))
            db.flush()
            for name in ["Dev", "Test", "Staging", "QA", "Production"]:
                db.add(Environment(organization_id=actor.tenant, name=name))
        if db.get(Administrator, (actor.tenant, actor.object_id)):
            parser.error("This administrator is already registered")
        db.add(Administrator(organization_id=actor.tenant, object_id=actor.object_id))
        audit(db, actor, "administrator.bootstrapped", actor.object_id)
    print("Administrator registered. No exercise connections or permissions were activated.")
