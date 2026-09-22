"""Real SQL Server only. No in-memory or SQLite replacement is supported."""

import os
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

from flood_lab.auth import Actor
from flood_lab.database import make_engine, sessions, utc_now, verify_database
from flood_lab.models import DatabaseIdentity
from flood_lab.operator import Operator, migrate
from flood_lab.service import LabService


@pytest.fixture(scope="session")
def sql_engine():
    url = os.environ.get("FLOOD_LAB_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Real SQL gate not run: FLOOD_LAB_TEST_DATABASE_URL is unset; no substitution.")
    name = os.environ.get("FLOOD_LAB_TEST_DATABASE_NAME", "")
    if (
        not name.startswith("flood_lab_test_")
        or os.environ.get("FLOOD_LAB_TEST_ALLOW_RESET") != "yes"
    ):
        pytest.fail(
            "SQL tests require a dedicated disposable flood_lab_test_ DB and explicit consent."
        )
    engine = make_engine(url, name)
    migrate(engine, name)
    with sessions(engine)() as session:
        verify_database(session, name)
        assert session.get(DatabaseIdentity, 1).purpose == "disposable-tests"
    yield engine
    engine.dispose()


@dataclass
class SQLLab:
    engine: Engine
    operator: Operator
    service: LabService
    manifest: dict
    participant: Actor
    api_actor: Actor
    observer: Actor
    expires_at: object
    run_key: str

    @property
    def run_id(self):
        return UUID(self.manifest["run_id"])

    @property
    def request_id(self):
        return UUID(self.manifest["requests"][0]["id"])

    @property
    def shelter_id(self):
        return UUID(self.manifest["shelters"][0]["id"])


@pytest.fixture
def sql_lab(sql_engine):
    factory = sessions(sql_engine)
    name = os.environ["FLOOD_LAB_TEST_DATABASE_NAME"]
    operator, service = Operator(factory, name), LabService(factory, name)
    run_key = "test-only-" + uuid4().hex
    manifest = operator.seed(run_key, apply=True)["manifest"]
    run_id = UUID(manifest["run_id"])
    tenant = uuid4()
    participant = Actor(tenant, uuid4(), "user")
    api_actor = Actor(tenant, uuid4(), "service")
    observer = Actor(tenant, uuid4(), "user")
    with factory() as session:
        expiry = utc_now(session) + timedelta(hours=2)
    for actor, role in ((participant, "participant"), (api_actor, "api"), (observer, "observer")):
        operator.grant(
            run_id, actor.tenant_id, actor.object_id, actor.kind, role, expiry, apply=True
        )
    yield SQLLab(
        sql_engine, operator, service, manifest, participant, api_actor, observer, expiry, run_key
    )
    # Deliberately retain TEST ONLY evidence. The explicitly selected database is disposable.
