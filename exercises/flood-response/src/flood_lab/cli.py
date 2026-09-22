from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from flood_lab.config import Settings, SetupRequired, database_target
from flood_lab.contracts import OperationError, parse_version, require_utc
from flood_lab.database import make_engine, sessions
from flood_lab.operator import Operator, migrate


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="EXERCISE ONLY: isolated flood lab operator. Mutations preview unless --apply."
    )
    actions = root.add_subparsers(dest="command", required=True)
    migration = actions.add_parser("migrate", help="Preview or apply independent lab migrations")
    migration.add_argument("--apply", action="store_true")
    seed = actions.add_parser("seed", help="Preview or create a deterministic synthetic run")
    seed.add_argument("--run-key", required=True)
    seed.add_argument("--apply", action="store_true")
    recovery = actions.add_parser(
        "recover", help="Retire unchanged seed-owned data; never purge evidence"
    )
    recovery.add_argument("--run-id", type=UUID, required=True)
    recovery.add_argument("--owner-operation", type=UUID, required=True)
    recovery.add_argument("--apply", action="store_true")
    grant = actions.add_parser("grant", help="Set or revoke a trusted Entra actor/run grant")
    grant.add_argument("--run-id", type=UUID, required=True)
    grant.add_argument("--tenant-id", type=UUID, required=True)
    grant.add_argument("--object-id", type=UUID, required=True)
    grant.add_argument("--principal-kind", choices=["user", "service"], required=True)
    grant.add_argument("--role", choices=["participant", "api", "observer"], required=True)
    grant.add_argument("--expires-at", type=datetime.fromisoformat, required=True)
    grant.add_argument("--revoke", action="store_true")
    grant.add_argument("--apply", action="store_true")
    sql_grant = actions.add_parser(
        "sql-grant", help="Grant existing SQL principal run-scoped access"
    )
    sql_grant.add_argument("--run-id", type=UUID, required=True)
    sql_grant.add_argument("--principal-name", required=True)
    sql_grant.add_argument("--capability", choices=["observe", "inject"], required=True)
    sql_grant.add_argument("--expires-at", type=datetime.fromisoformat, required=True)
    sql_grant.add_argument("--apply", action="store_true")
    for name in ("read-occupancy", "inject-occupancy"):
        occupancy = actions.add_parser(name)
        occupancy.add_argument("--run-id", type=UUID, required=True)
        occupancy.add_argument("--shelter-id", type=UUID, required=True)
        if name == "inject-occupancy":
            occupancy.add_argument("--occupancy", type=int, required=True)
            occupancy.add_argument("--expected-version", required=True)
            occupancy.add_argument("--idempotency-key", required=True)
            occupancy.add_argument("--apply", action="store_true")
    return root


def occupancy(settings: Settings, args: argparse.Namespace) -> dict:
    write = args.command == "inject-occupancy"
    env = "FLOOD_LAB_INJECTOR_DATABASE_URL" if write else "FLOOD_LAB_OBSERVER_DATABASE_URL"
    url = os.environ.get(env, "")
    database_target(url, settings.database_name)
    params = {"run_id": str(args.run_id), "shelter_id": str(args.shelter_id)}
    if write:
        parse_version(args.expected_version)
        if not 0 <= args.occupancy <= 100000:
            raise ValueError("Occupancy is out of bounds.")
        if not args.apply:
            return {
                "outcome": "preview",
                **params,
                "occupancy": args.occupancy,
                "expected_version": args.expected_version,
                "idempotency_key": args.idempotency_key,
                "procedure": "flood.UpdateOccupancy",
            }
        params.update(
            occupancy=args.occupancy,
            expected_version=args.expected_version,
            idempotency_key=args.idempotency_key,
        )
        statement = text(
            "EXEC flood.UpdateOccupancy @run_id=:run_id, @shelter_id=:shelter_id, "
            "@occupancy=:occupancy, @expected_version=:expected_version, "
            "@idempotency_key=:idempotency_key"
        )
    else:
        statement = text("EXEC flood.ReadOccupancy @run_id=:run_id, @shelter_id=:shelter_id")
    engine = make_engine(url, settings.database_name)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            if connection.scalar(text("SELECT DB_NAME()")) != settings.database_name:
                raise SetupRequired("Dedicated database mismatch.")
            row = connection.execute(statement, params).mappings().one()
            return dict(row)
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    engine = None
    try:
        settings = Settings()
        if args.command in {"read-occupancy", "inject-occupancy"}:
            result = occupancy(settings, args)
        else:
            settings.database(operator=True)
            engine = make_engine(settings.operator_database_url, settings.database_name)
            operator = Operator(sessions(engine), settings.database_name)
            if args.command == "migrate":
                if args.apply:
                    migrate(engine, settings.database_name)
                result = {
                    "outcome": "succeeded" if args.apply else "preview",
                    "database": settings.database_name,
                    "migration": "0002_occupancy_percentage",
                    "message": (
                        "Dedicated DB only. No database creation, GT change or cloud change."
                    ),
                }
            elif args.command == "seed":
                result = operator.seed(args.run_key, apply=args.apply)
            elif args.command == "recover":
                result = operator.recover(args.run_id, args.owner_operation, apply=args.apply)
            elif args.command == "grant":
                result = operator.grant(
                    args.run_id,
                    args.tenant_id,
                    args.object_id,
                    args.principal_kind,
                    args.role,
                    require_utc(args.expires_at),
                    revoke=args.revoke,
                    apply=args.apply,
                )
            else:
                result = operator.sql_grant(
                    args.run_id,
                    args.principal_name,
                    args.capability,
                    require_utc(args.expires_at),
                    apply=args.apply,
                )
        print(json.dumps(result, indent=2, default=str))
        return 2 if result.get("outcome") in {"partial", "rejected"} else 0
    except OperationError as error:
        print(json.dumps({"outcome": error.outcome, "code": error.code, "message": error.message}))
        return 2
    except (ValueError, PermissionError, SetupRequired) as error:
        print(json.dumps({"outcome": "failed", "message": str(error)}))
        return 2
    except (SQLAlchemyError, TimeoutError):
        print(
            json.dumps(
                {
                    "outcome": "unknown" if getattr(args, "apply", False) else "failed",
                    "message": (
                        "Database operation could not be confirmed. Inspect durable evidence; "
                        "retry identical seed/operation inputs, never a new mutation key."
                    ),
                }
            )
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
