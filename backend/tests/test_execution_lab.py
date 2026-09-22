"""Real lab SQL and HTTPS, isolated processes/dependencies; identity exchange is a fixture."""

import json
import os
import ssl
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from test_preparation_sql import preview, published_board, require

from gametheory import execution_adapters as adapters
from gametheory import execution_service as service
from gametheory import execution_worker as worker
from gametheory.config import Settings
from gametheory.execution_adapters import AllowedOperation, TargetBinding
from gametheory.persistence import RunDispatch, RunStep, TargetReadiness, now
from gametheory.preparation import canonical_digest

pytestmark = pytest.mark.integration


@pytest.fixture
def live_lab(tmp_path):
    if not os.environ.get("GT_TEST_EXECUTION_LAB"):
        pytest.skip(
            "Independent lab process is not configured; SQL/HTTPS cross-package gate not executed"
        )
    root = Path(__file__).resolve().parents[2]
    lab = root / "exercises/flood-response"
    executable = lab / ".venv/bin/python"
    assert executable.exists(), "Install the lab's independent locked environment"
    artifact = tmp_path / "lab-fixture.json"
    log = (tmp_path / "lab-fixture.log").open("w")
    process = subprocess.Popen(
        [str(executable), str(lab / "tests/execution_host.py"), "--output", str(artifact)],
        env={**os.environ, "PYTHONPATH": str(lab / "src")},
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 120
        metadata = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Independent lab fixture exited; inspect its diagnostic log")
            if artifact.exists():
                metadata = json.loads(artifact.read_text())
                context = ssl.create_default_context(cafile=metadata["certificate"])
                try:
                    if (
                        httpx.get(
                            metadata["api_origin"] + "/health", verify=context, trust_env=False
                        ).status_code
                        == 200
                    ):
                        break
                except httpx.TransportError:
                    pass
            time.sleep(0.25)
        else:
            pytest.fail("Independent SQL/HTTPS lab did not become responsive")
        assert metadata is not None
        yield SimpleNamespace(**metadata, ssl_context=context, root=lab)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        artifact.with_suffix(".key").unlink(missing_ok=True)


def test_real_lab_effects_reconcile_after_commit_and_participants_remain_external(
    sql_client,
    sql_factory,
    monkeypatch,
    tmp_path,
    live_lab,
):
    client, actor = sql_client
    lab = live_lab
    target_url = os.environ["FLOOD_LAB_TEST_DATABASE_URL"]
    database = os.environ["FLOOD_LAB_TEST_DATABASE_NAME"]
    assert database.startswith("flood_lab_test_") and make_url(target_url).database == database
    assert make_url(target_url).host in {"127.0.0.1", "localhost"}
    wid, workspace, _, _, board, board_path = published_board(
        client, "Isolated cross-package exercise"
    )
    environment = require(
        client.post("/api/environments", json={"name": "Disposable fixture"}), 201
    )
    require(
        client.put(
            f"/api/admin/environment-policies/{environment['id']}",
            headers={"If-Match": '"1"'},
            json={
                "classification": "nonproduction",
                "execution_enabled": True,
                "approval_required": False,
            },
        )
    )
    require(
        client.put(
            workspace + "/execution-grants",
            json={"object_id": actor.object_id, "capability": "operator"},
        )
    )
    configs, targets = {}, []
    for kind in ("sql", "rest"):
        catalog = json.loads((lab.root / f"assets/operation-catalog-{kind}.json").read_text())
        if kind == "rest":
            catalog["operations"] = [
                item
                for item in catalog["operations"]
                if item["key"] in {"resource-request.create", "resource-request.read"}
            ]
        connection = require(
            client.post(
                workspace + "/connections",
                json={
                    "name": f"Fixture {kind}",
                    "kind": kind,
                    "scope": "workspace",
                    "environment_id": environment["id"],
                },
            ),
            201,
        )
        config = require(
            client.post(
                workspace + f"/connections/{connection['id']}/configurations",
                json={
                    "classification": "nonproduction",
                    "resource_id": f"fixture/{kind}",
                    "endpoint": "127.0.0.1" if kind == "sql" else lab.api_origin,
                    "database": database if kind == "sql" else "",
                    "identity_ref": f"fixture/{kind}/identity",
                    "catalog": catalog,
                },
            ),
            201,
        )
        configs[kind] = config
        targets.append(
            TargetBinding(
                configuration_id=config["id"],
                configuration_digest=config["digest"],
                environment_id=environment["id"],
                classification="nonproduction",
                resource_id=f"fixture/{kind}",
                endpoint=config["content"]["endpoint"],
                database=config["content"]["database"],
                identity_ref=config["content"]["identity_ref"],
                client_id=uuid4(),
                token_scope="api://fixture-only/.default" if kind == "rest" else "",
                operations=[
                    AllowedOperation(digest=canonical_digest(item), safe_replay=True)
                    for item in config["content"]["catalog"]["operations"]
                ],
            )
        )
    rid = lab.manifest["run_id"]
    shelter = lab.manifest["shelters"][0]
    read, inject, request, observe = [str(uuid4()) for _ in range(4)]

    def bound(kind, key, version):
        return {
            "configuration_id": configs[kind]["id"],
            "operation_key": key,
            "operation_version": version,
        }

    current = datetime.now(UTC)
    steps = [
        {
            "id": read,
            "label": "Read SQL shelter",
            "kind": "operation",
            "binding": bound("sql", "shelter.occupancy.read", "2"),
            "parameters": {"run_id": rid, "shelter_id": shelter["id"]},
        },
        {
            "id": inject,
            "label": "Inject SQL occupancy",
            "kind": "operation",
            "depends_on": [read],
            "binding": bound("sql", "shelter.occupancy.update", "2"),
            "parameters": {
                "run_id": rid,
                "shelter_id": shelter["id"],
                "occupancy": int(shelter["capacity"] * 0.9),
                "expected_version": {"source_step_id": read, "field": "record_version"},
            },
        },
        {
            "id": request,
            "label": "Create API request",
            "kind": "operation",
            "depends_on": [inject],
            "binding": bound("rest", "resource-request.create", "1"),
            "parameters": {
                "run_id": rid,
                "shelter_id": shelter["id"],
                "resource_type": "cots",
                "quantity_requested": 5,
                "summary": "EXERCISE ONLY: isolated executor integration",
                "needed_by": (current + timedelta(minutes=30)).isoformat(),
            },
        },
        {
            "id": observe,
            "label": "Observe human response",
            "kind": "operation",
            "depends_on": [request],
            "binding": bound("rest", "resource-request.read", "1"),
            "parameters": {
                "run_id": rid,
                "request_id": {"source_step_id": request, "field": "request_id"},
            },
        },
    ]
    saved = require(
        client.put(
            board_path,
            headers={"If-Match": '"1"'},
            json={
                **board["draft"],
                "steps": steps,
                "window": {
                    "starts_at": (current - timedelta(minutes=1)).isoformat(),
                    "ends_at": (current + timedelta(hours=1)).isoformat(),
                },
                "recovery": "Use the independent operator's conflict-safe recovery and record retained changes.",
            },
        )
    )
    frozen = preview(client, board_path, saved["version"])
    binding_file = tmp_path / "cross-package-bindings.json"
    binding_file.write_text(json.dumps([item.model_dump(mode="json") for item in targets]))
    settings = Settings(
        _env_file=None,
        sql_url=os.environ["GT_TEST_SQL_URL"],
        tenant_id=actor.tenant,
        scheduler_endpoint="http://127.0.0.1:8080",
        scheduler_emulator=True,
        execution_taskhub="default",
        execution_enabled=True,
        execution_bindings_file=str(binding_file),
    )
    for module in (adapters, service, worker):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "session_factory", lambda: sql_factory)
    with sql_factory.begin() as db:
        for target in targets:
            db.add(
                TargetReadiness(
                    configuration_id=str(target.configuration_id),
                    configuration_digest=target.configuration_digest,
                    binding_digest=target.digest,
                    evidence_reference="fixture:real-sql-https-with-fixture-identity",
                    actor="sql:fixture",
                    checked_at=now(),
                    expires_at=now() + timedelta(hours=2),
                )
            )
    real_engine = create_engine

    def target_engine(url, **_kwargs):
        assert url.database == database and url.host == "127.0.0.1"
        engine = real_engine(target_url)

        @event.listens_for(engine, "connect")
        def impersonate(connection, _record):
            cursor = connection.cursor()
            cursor.execute(f"EXECUTE AS USER = '{lab.sql_principal}'")
            cursor.close()

        return engine

    monkeypatch.setattr(adapters, "create_engine", target_engine)
    real_http_client = httpx.Client
    monkeypatch.setattr(
        adapters.httpx,
        "Client",
        lambda **kwargs: real_http_client(verify=lab.ssl_context, **kwargs),
    )

    class FixtureCredential:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get_token(self, scope):
            assert scope == "api://fixture-only/.default"
            return SimpleNamespace(token="fixture-service")

    monkeypatch.setattr(adapters, "ManagedIdentityCredential", FixtureCredential)
    run = require(
        client.post(
            board_path + "/runs",
            headers={"If-Match": f'"{saved["version"]}"'},
            json={
                "preview_id": frozen["id"],
                "preview_digest": frozen["digest"],
                "observations": [
                    {
                        "step_id": observe,
                        "field": "status",
                        "operator": "eq",
                        "value": "acknowledged",
                        "interval_seconds": 1,
                        "timeout_seconds": 30,
                        "max_samples": 5,
                    }
                ],
            },
        ),
        201,
    )
    run_path = workspace + f"/runs/{run['id']}"

    def control(action):
        current_run = require(client.get(run_path))
        return require(
            client.post(
                run_path + "/controls",
                headers={"If-Match": f'"{current_run["version"]}"'},
                json={"action": action, "note": "Approved disposable cross-package test"},
            )
        )

    control("authorize")
    control("start")
    with sql_factory() as db:
        did = db.scalar(select(RunDispatch.id).where(RunDispatch.run_id == run["id"]))
    worker.advance(did)
    original_invoke = worker.invoke

    def crash_after_commit(*args):
        result = original_invoke(*args)
        assert result.outcome == "succeeded", result.reason
        raise OperationalError("fixture: lost result after target commit", {}, Exception("fixture"))

    monkeypatch.setattr(worker, "invoke", crash_after_commit)
    with pytest.raises(OperationalError):
        worker.advance(did)
    with sql_factory.begin() as db:
        step = db.get(RunStep, (run["id"], "exercise", inject))
        step.lease_until = now() - timedelta(seconds=1)
    monkeypatch.setattr(worker, "invoke", original_invoke)
    worker.advance(did)
    assert require(client.get(run_path))["state"] == "intervention"
    control("authorize")
    control("reconcile")
    worker.advance(did)
    worker.advance(did)
    current_run = require(client.get(run_path))
    created = next(item for item in current_run["steps"] if item["step_id"] == request)
    assert created["state"] == "succeeded", created["reason"]
    with real_http_client(verify=lab.ssl_context, trust_env=False) as participant:
        response = participant.post(
            lab.api_origin
            + f"/v1/runs/{rid}/requests/{created['result']['request_id']}/acknowledge",
            headers={
                "Authorization": "Bearer fixture-participant",
                "Idempotency-Key": "participant-fixture",
                "If-Match": '"' + created["result"]["record_version"] + '"',
            },
            json={},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "acknowledged"
    worker.advance(did)
    assert worker.advance(did)["done"] is True
    final = require(client.get(run_path))
    assert final["state"] == "completed"
    assert (
        next(item for item in final["steps"] if item["step_id"] == observe)["result"]["status"]
        == "acknowledged"
    )
    inspection = real_engine(target_url)
    try:
        with inspection.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT COUNT(*) FROM flood.receipts WHERE run_id=:run AND operation='shelter.occupancy.update'"
                    ),
                    {"run": rid},
                )
                == 1
            )
    finally:
        inspection.dispose()
