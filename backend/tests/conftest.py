import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from gametheory.api import app
from gametheory.auth import Principal, authenticate
from gametheory.config import get_settings
from gametheory.persistence import Administrator, Organization, get_db


@pytest.fixture(scope="session")
def sql_factory():
    url = os.environ.get("GT_TEST_SQL_URL")
    if not url:
        pytest.skip("GT_TEST_SQL_URL is not configured; real SQL integration was not executed")
    database = make_url(url).database or ""
    if not database.startswith("gametheory_test"):
        pytest.fail("Integration tests require a dedicated database named gametheory_test*")
    engine = create_engine(url, pool_pre_ping=True)
    previous = os.environ.get("GT_SQL_URL")
    os.environ["GT_SQL_URL"] = url
    get_settings.cache_clear()
    try:
        command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
        yield sessionmaker(engine)
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("GT_SQL_URL", None)
        else:
            os.environ["GT_SQL_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture
def sql_client(sql_factory):
    actor = Principal(str(uuid4()), str(uuid4()))
    with sql_factory.begin() as db:
        db.add(Organization(id=actor.tenant, name="Isolated integration test"))
        db.flush()
        db.add(Administrator(organization_id=actor.tenant, object_id=actor.object_id))

    def db_dependency():
        with sql_factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[authenticate] = lambda: actor
    with TestClient(app) as client:
        yield client, actor
    app.dependency_overrides.clear()
