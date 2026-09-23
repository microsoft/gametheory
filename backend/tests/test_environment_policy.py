from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import mssql
from sqlalchemy.schema import CreateTable

from gametheory import environment_policy as service
from gametheory.auth import Principal
from gametheory.environment_policy import EnvironmentPolicyInput
from gametheory.persistence import EnvironmentPolicyRecord, now


@pytest.mark.parametrize(
    "classification,required,enabled,valid",
    [
        ("production", True, True, True),
        ("production", False, False, False),
        ("nonproduction", False, True, True),
        ("nonproduction", True, True, True),
        ("unknown", True, False, True),
        ("unknown", False, True, False),
    ],
)
def test_policy_boundary(classification, required, enabled, valid):
    value = dict(
        classification=classification, approval_required=required, execution_enabled=enabled
    )
    if valid:
        assert EnvironmentPolicyInput.model_validate(value).model_dump() == value
    else:
        with pytest.raises(ValidationError):
            EnvironmentPolicyInput.model_validate(value)


def test_policy_does_not_coerce_approval_or_allow_unknown_settings():
    for value in (
        {"classification": "production", "approval_required": "true"},
        {"classification": "nonproduction", "bypass": True},
    ):
        with pytest.raises(ValidationError):
            EnvironmentPolicyInput.model_validate(value)


def test_database_enforces_production_and_unknown_boundaries():
    sql = str(CreateTable(EnvironmentPolicyRecord.__table__).compile(dialect=mssql.dialect()))
    assert "classification <> 'production' OR approval_required = 1" in sql
    assert "classification <> 'unknown' OR execution_enabled = 0" in sql


def test_policy_lookup_is_current_and_fenced():
    db = MagicMock()
    service.latest_policy(db, str(uuid4()))
    sql = str(db.scalar.call_args.args[0].compile(dialect=mssql.dialect()))
    assert "WITH (HOLDLOCK)" in sql
    assert "version DESC" in sql
    db.scalar.return_value = None
    with pytest.raises(HTTPException, match="migration"):
        service.latest_policy(db, str(uuid4()))


@pytest.mark.parametrize("stale", [False, True])
def test_cannot_downgrade_production_or_overwrite_stale_policy(monkeypatch, stale):
    db = MagicMock()
    monkeypatch.setattr(service, "policy_environment", MagicMock())
    monkeypatch.setattr(
        service, "latest_policy", lambda *_: SimpleNamespace(version=3, classification="production")
    )
    with pytest.raises(HTTPException) as caught:
        service.save_policy(
            db,
            Principal(str(uuid4()), str(uuid4())),
            str(uuid4()),
            EnvironmentPolicyInput(classification="nonproduction"),
            2 if stale else 3,
            str(uuid4()),
        )
    assert caught.value.status_code == (409 if stale else 422)
    db.add.assert_not_called()


def test_update_appends_history_without_rewriting_previous_policy(monkeypatch):
    eid, actor = str(uuid4()), Principal(str(uuid4()), str(uuid4()))
    previous = EnvironmentPolicyRecord(
        environment_id=eid,
        version=1,
        classification="nonproduction",
        execution_enabled=False,
        approval_required=False,
        actor=actor.object_id,
        created_at=now(),
    )
    db = MagicMock()
    monkeypatch.setattr(
        service, "policy_environment", lambda *_args, **_kwargs: SimpleNamespace(id=eid, name="QA")
    )
    monkeypatch.setattr(service, "latest_policy", lambda *_: previous)
    db.flush.side_effect = lambda: setattr(db.add.call_args.args[0], "created_at", now())
    result = service.save_policy(
        db,
        actor,
        eid,
        EnvironmentPolicyInput(
            classification="nonproduction", execution_enabled=True, approval_required=True
        ),
        1,
        str(uuid4()),
    )
    assert result.version == 2
    assert result.approval_required
    assert previous.version == 1 and not previous.approval_required
    db.delete.assert_not_called()


def test_policy_mutation_requires_fenced_admin_and_organization(monkeypatch):
    db = MagicMock()
    actor = Principal(str(uuid4()), str(uuid4()))
    admin = MagicMock()
    monkeypatch.setattr(service, "require_admin", admin)
    service.policy_environment(db, actor, str(uuid4()), mutation=True)
    admin.assert_called_once_with(db, actor, fence=True)
    sql = str(db.scalar.call_args.args[0].compile(dialect=mssql.dialect()))
    assert "WITH (UPDLOCK, HOLDLOCK)" in sql
    assert "organization_id =" in sql


@pytest.mark.integration
def test_environment_policy_api_uses_real_sql_and_resource_preconditions(sql_client):
    client, _ = sql_client
    eid = client.post("/api/environments", json={"name": "Policy integration"}).json()["id"]
    path = f"/api/admin/environment-policies/{eid}"
    initial = client.get(path)
    assert initial.status_code == 200
    assert initial.headers["etag"] == '"1"'
    assert initial.json()["classification"] == "unknown"
    assert not initial.json()["execution_enabled"]
    body = {
        "classification": "nonproduction",
        "execution_enabled": True,
        "approval_required": False,
    }
    assert client.put(path, json=body).status_code == 428
    saved = client.put(path, json=body, headers={"If-Match": '"1"'})
    assert saved.status_code == 200
    assert saved.headers["etag"] == '"2"'
    assert not saved.json()["approval_required"]
    assert client.put(path, json=body, headers={"If-Match": '"1"'}).status_code == 409
    assert (
        client.put(
            path, json={**body, "classification": "production"}, headers={"If-Match": '"2"'}
        ).status_code
        == 422
    )
    production = client.put(
        path,
        json={**body, "classification": "production", "approval_required": True},
        headers={"If-Match": '"2"'},
    )
    assert production.status_code == 200
    assert client.put(path, json=body, headers={"If-Match": '"3"'}).status_code == 422
    history = client.get(f"{path}/history").json()
    assert [item["version"] for item in history] == [3, 2, 1]
    assert client.get("/api/admin/environment-policies").headers.get("etag") is None


@pytest.mark.integration
def test_nonadmin_and_other_tenant_cannot_manage_policy(sql_client, sql_factory):
    from gametheory.api import app
    from gametheory.auth import authenticate
    from gametheory.persistence import Administrator, Organization

    client, actor = sql_client
    eid = client.post("/api/environments", json={"name": "Private policy"}).json()["id"]
    app.dependency_overrides[authenticate] = lambda: Principal(actor.tenant, str(uuid4()))
    assert client.get("/api/admin/environment-policies").status_code == 403
    assert client.get("/api/admin/runtime").status_code == 403
    outsider = Principal(str(uuid4()), str(uuid4()))
    with sql_factory.begin() as db:
        db.add(Organization(id=outsider.tenant, name="Other tenant"))
        db.flush()
        db.add(Administrator(organization_id=outsider.tenant, object_id=outsider.object_id))
    app.dependency_overrides[authenticate] = lambda: outsider
    assert client.get(f"/api/admin/environment-policies/{eid}").status_code == 404
