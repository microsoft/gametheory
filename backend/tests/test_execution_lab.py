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
# TEST ONLY windows, supplied as ordinary read parameters. The lab profile keeps 600/1200.
WINDOWS = {"acknowledge_within_seconds": 4, "allocate_within_seconds": 6}


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
        yield SimpleNamespace(**metadata, ssl_context=context, root=lab, process=process)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
        artifact.with_suffix(".key").unlink(missing_ok=True)


def objective_board(client, name, objectives):
    """Publish ordinary authored objectives, then create a board from that pinned revision."""
    if not objectives:
        _, workspace, _, _, board, board_path = published_board(client, name)
        return workspace, board, board_path, {}
    wid = require(client.post("/api/workspaces", json={"name": "Execution evidence"}), 201)["id"]
    workspace = f"/api/workspaces/{wid}"
    scenario = require(client.post(workspace + "/scenarios", json={"name": name}), 201)
    scenario_path = workspace + f"/scenarios/{scenario['id']}"
    ids = {key: str(uuid4()) for key in objectives}
    content = scenario["content"] | {
        "objectives": [
            {
                "id": ids[key],
                "title": title,
                "criterion": "EXERCISE ONLY: assessed from bound authoritative lab evidence.",
            }
            for key, title in objectives.items()
        ]
    }
    saved = require(client.put(scenario_path, headers={"If-Match": '"1"'}, json=content))
    require(
        client.post(scenario_path + "/revisions", headers={"If-Match": f'"{saved["version"]}"'}),
        201,
    )
    board = require(
        client.post(
            workspace + "/boards",
            json={
                "name": name,
                "scenario_id": scenario["id"],
                "revision_version": saved["version"],
            },
        ),
        201,
    )
    return workspace, board, workspace + f"/boards/{board['id']}", ids


def lab_case(
    sql_client,
    sql_factory,
    monkeypatch,
    tmp_path,
    lab,
    name,
    *,
    rest=(),
    sql=False,
    objectives=None,
):
    """Register the lab's own catalogs through ordinary product APIs; authority is a fixture."""
    client, actor = sql_client
    target_url = os.environ.get("FLOOD_LAB_TEST_DATABASE_URL", "")
    database = os.environ.get("FLOOD_LAB_TEST_DATABASE_NAME", "")
    if sql:
        assert database.startswith("flood_lab_test_") and make_url(target_url).database == database
        assert make_url(target_url).host in {"127.0.0.1", "localhost"}
    workspace, board, board_path, pinned = objective_board(client, name, objectives or {})
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
    for kind in (["sql"] if sql else []) + (["rest"] if rest else []):
        catalog = json.loads((lab.root / f"assets/operation-catalog-{kind}.json").read_text())
        if kind == "rest":
            catalog["operations"] = [item for item in catalog["operations"] if item["key"] in rest]
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
    if sql:

        def target_engine(url, **_kwargs):
            assert url.database == database and url.host == "127.0.0.1"
            engine = real_engine(target_url)

            @event.listens_for(engine, "checkout")
            def impersonate(connection, _record, _proxy):
                cursor = connection.cursor()
                cursor.execute(f"EXECUTE AS USER = '{lab.sql_principal}'")
                cursor.close()

            @event.listens_for(engine, "reset")
            def restore_principal(connection, _record, _state):
                # ODBC pooling can outlive this SQLAlchemy engine and retain EXECUTE AS.
                cursor = connection.cursor()
                cursor.execute("REVERT")
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

    def bound(kind, key, version):
        return {
            "configuration_id": configs[kind]["id"],
            "operation_key": key,
            "operation_version": version,
        }

    return SimpleNamespace(
        client=client,
        sql_factory=sql_factory,
        workspace=workspace,
        board=board,
        board_path=board_path,
        objectives=pinned,
        bound=bound,
        target_url=target_url,
        real_engine=real_engine,
        real_http_client=real_http_client,
        lab=lab,
    )


def save_draft(case, steps):
    board = require(case.client.get(case.board_path))
    current = datetime.now(UTC)
    saved = require(
        case.client.put(
            case.board_path,
            headers={"If-Match": f'"{board["version"]}"'},
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
    return saved, preview(case.client, case.board_path, saved["version"])


def run_control(client, path, action):
    current = require(client.get(path))
    return require(
        client.post(
            path + "/controls",
            headers={"If-Match": f'"{current["version"]}"'},
            json={"action": action, "note": "Approved disposable cross-package test"},
        )
    )


def start_run(case, saved, frozen, *, observations=(), objectives=()):
    run = require(
        case.client.post(
            case.board_path + "/runs",
            headers={"If-Match": f'"{saved["version"]}"'},
            json={
                "preview_id": frozen["id"],
                "preview_digest": frozen["digest"],
                "observations": list(observations),
                "objectives": list(objectives),
            },
        ),
        201,
    )
    path = case.workspace + f"/runs/{run['id']}"
    run_control(case.client, path, "authorize")
    run_control(case.client, path, "start")
    with case.sql_factory() as db:
        dispatch = db.scalar(select(RunDispatch.id).where(RunDispatch.run_id == run["id"]))
    return SimpleNamespace(id=run["id"], path=path, dispatch=dispatch)


def drive(case, run, until, seconds=90):
    """Advance one durable step at a time, sleeping only for the executor's requested delay."""
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        tick = worker.advance(run.dispatch)
        current = require(case.client.get(run.path))
        if until(current):
            return current
        if tick["done"]:
            pytest.fail(f"Run ended as {current['state']} before the expected evidence")
        time.sleep(min(max(tick["delay"], 0.05), 1))
    pytest.fail("Run did not reach the expected evidence in time")


def completed(current):
    return current["state"] == "completed"


def step(current, sid):
    return next(
        item for item in current["steps"] if item["step_id"] == sid and item["phase"] == "exercise"
    )


def verdicts(case, current):
    names = {value: key for key, value in case.objectives.items()}
    return {names[item["objective_id"]]: item["state"] for item in current["findings"]}


def observed(current, sid):
    return [
        json.loads(item["detail"]["result"])
        for item in current["events"]
        if item["kind"] == "observation" and item["step_id"] == sid
    ]


def lab_http(case):
    return case.real_http_client(verify=case.lab.ssl_context, trust_env=False)


def participant(case, request, action, version, body=None):
    """The participant acts directly in the independent lab, never through the product."""
    with lab_http(case) as http:
        response = http.post(
            case.lab.api_origin
            + f"/v1/runs/{case.lab.manifest['run_id']}/requests/{request}/{action}",
            headers={
                "Authorization": "Bearer fixture-participant",
                "Idempotency-Key": f"participant-{action}-{uuid4().hex}",
                "If-Match": f'"{version}"',
            },
            json=body or {},
        )
    assert response.status_code == 200, response.text
    return response.json()


def past_lab_deadline(case, request, deadline):
    """TEST ONLY: wait on the lab's own clock, not the product clock, until a deadline passes."""
    limit = time.monotonic() + 30
    while time.monotonic() < limit:
        with lab_http(case) as http:
            response = http.get(
                case.lab.api_origin
                + f"/v1/runs/{case.lab.manifest['run_id']}/requests/{request}/milestones",
                headers={"Authorization": "Bearer fixture-participant"},
                params=WINDOWS,
            )
        assert response.status_code == 200, response.text
        view = response.json()
        if datetime.fromisoformat(view["as_of"]) > datetime.fromisoformat(view[deadline]):
            return view
        time.sleep(0.2)
    pytest.fail("The lab clock did not pass the TEST ONLY deadline")


def milestone_draft(case):
    """Create a request, then read its bounded milestones with TEST ONLY windows."""
    rid, shelter = case.lab.manifest["run_id"], case.lab.manifest["shelters"][0]
    create, milestones = str(uuid4()), str(uuid4())
    saved, frozen = save_draft(
        case,
        [
            {
                "id": create,
                "label": "Create API request",
                "kind": "operation",
                "binding": case.bound("rest", "resource-request.create", "1"),
                "parameters": {
                    "run_id": rid,
                    "shelter_id": shelter["id"],
                    "resource_type": "cots",
                    "quantity_requested": 5,
                    "summary": "EXERCISE ONLY: authoritative response timing",
                    "needed_by": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                },
            },
            {
                "id": milestones,
                "label": "Observe authoritative milestones",
                "kind": "operation",
                "depends_on": [create],
                "binding": case.bound("rest", "resource-request.milestones", "1"),
                "parameters": {
                    "run_id": rid,
                    "request_id": {"source_step_id": create, "field": "request_id"},
                    **WINDOWS,
                },
            },
        ],
    )
    return SimpleNamespace(create=create, milestones=milestones, saved=saved, frozen=frozen)


def milestone_policy(draft, field, interval, samples):
    return {
        "step_id": draft.milestones,
        "field": field,
        "operator": "eq",
        "value": True,
        "interval_seconds": interval,
        "timeout_seconds": 60,
        "max_samples": samples,
    }


def created_request(case, draft, run):
    current = drive(case, run, lambda view: step(view, draft.create)["state"] == "succeeded")
    return step(current, draft.create)["result"]


def test_real_lab_effects_reconcile_after_commit_and_participants_remain_external(
    sql_client,
    sql_factory,
    monkeypatch,
    tmp_path,
    live_lab,
):
    lab = live_lab
    case = lab_case(
        sql_client,
        sql_factory,
        monkeypatch,
        tmp_path,
        lab,
        "Isolated cross-package exercise",
        rest={"resource-request.create", "resource-request.read"},
        sql=True,
    )
    client, workspace, board, board_path = case.client, case.workspace, case.board, case.board_path
    bound, target_url = case.bound, case.target_url
    real_engine, real_http_client = case.real_engine, case.real_http_client
    rid = lab.manifest["run_id"]
    shelter = lab.manifest["shelters"][0]
    read, inject, request, observe = [str(uuid4()) for _ in range(4)]

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


def test_real_lab_milestones_assess_timed_objectives_from_authoritative_evidence(
    sql_client,
    sql_factory,
    monkeypatch,
    tmp_path,
    live_lab,
):
    case = lab_case(
        sql_client,
        sql_factory,
        monkeypatch,
        tmp_path,
        live_lab,
        "Authoritative response timing",
        rest={"resource-request.create", "resource-request.milestones"},
        objectives={
            "acknowledged": "Acknowledge within the TEST ONLY window",
            "source": "Acknowledgement source time within the TEST ONLY window",
            "allocated": "Allocate the requested quantity within the TEST ONLY window",
        },
    )
    draft = milestone_draft(case)
    on_time = {
        "objective_id": case.objectives["acknowledged"],
        "step_id": draft.milestones,
        "field": "acknowledged_on_time",
        "operator": "eq",
        "value": True,
    }
    source_time = {
        "objective_id": case.objectives["source"],
        "step_id": draft.milestones,
        "field": "acknowledged",
        "operator": "eq",
        "value": True,
        "anchor_step_id": draft.milestones,
        "anchor_field": "created_at",
        "within_seconds": WINDOWS["acknowledge_within_seconds"],
        "source_time_field": "acknowledged_at",
    }
    allocated = {
        "objective_id": case.objectives["allocated"],
        "step_id": draft.milestones,
        "field": "allocated_on_time",
        "operator": "eq",
        "value": True,
    }
    acknowledgement = milestone_policy(draft, "acknowledged_on_time", 1, 5)

    run = start_run(
        case,
        draft.saved,
        draft.frozen,
        observations=[acknowledgement],
        objectives=[on_time, source_time],
    )
    request = created_request(case, draft, run)
    participant(case, request["request_id"], "acknowledge", request["record_version"])
    final = drive(case, run, completed)
    result = step(final, draft.milestones)["result"]
    assert result["acknowledged_on_time"] is True
    assert result["created_event_id"] and result["acknowledgement_event_id"]
    assert verdicts(case, final) == {
        "acknowledged": "met",
        "source": "met",
        "allocated": "indeterminate",
    }

    run = start_run(
        case,
        draft.saved,
        draft.frozen,
        observations=[acknowledgement],
        objectives=[on_time, source_time],
    )
    request = created_request(case, draft, run)
    first = drive(case, run, lambda view: step(view, draft.milestones)["samples"] >= 1)
    assert step(first, draft.milestones)["result"]["acknowledged_on_time"] is None
    past_lab_deadline(case, request["request_id"], "acknowledgement_deadline")
    participant(case, request["request_id"], "acknowledge", request["record_version"])
    final = drive(case, run, completed)
    result = step(final, draft.milestones)["result"]
    assert result["acknowledged_on_time"] is False
    assert datetime.fromisoformat(result["acknowledged_at"]) > datetime.fromisoformat(
        result["acknowledgement_deadline"]
    )
    assert verdicts(case, final)["acknowledged"] == "unmet"
    assert verdicts(case, final)["source"] == "unmet"

    run = start_run(
        case,
        draft.saved,
        draft.frozen,
        observations=[milestone_policy(draft, "acknowledged_on_time", 2, 4)],
        objectives=[on_time, source_time],
    )
    created_request(case, draft, run)
    final = drive(case, run, completed)
    samples = observed(final, draft.milestones)
    passed = [
        datetime.fromisoformat(item["as_of"])
        > datetime.fromisoformat(item["acknowledgement_deadline"])
        for item in samples
    ]
    assert False in passed and True in passed
    assert all(item["acknowledged_on_time"] is None for item in samples)
    assert all(item["acknowledged"] is False for item in samples)
    assert verdicts(case, final)["acknowledged"] == "indeterminate"
    assert verdicts(case, final)["source"] == "indeterminate"

    run = start_run(
        case,
        draft.saved,
        draft.frozen,
        observations=[milestone_policy(draft, "allocated_on_time", 2, 5)],
        objectives=[on_time, allocated],
    )
    request = created_request(case, draft, run)
    acknowledged = participant(
        case, request["request_id"], "acknowledge", request["record_version"]
    )
    participant(
        case,
        request["request_id"],
        "allocate",
        acknowledged["record_version"],
        {"quantity": 2, "available_at": request["created_at"]},
    )
    final = drive(case, run, completed)
    result = step(final, draft.milestones)["result"]
    assert result["allocated_on_time"] is False
    assert result["allocated_total_by_deadline"] == 2
    assert datetime.fromisoformat(result["as_of"]) > datetime.fromisoformat(
        result["allocation_deadline"]
    )
    assert [item["allocated_on_time"] for item in observed(final, draft.milestones)][0] is None
    assert verdicts(case, final) == {
        "acknowledged": "met",
        "source": "indeterminate",
        "allocated": "unmet",
    }


def test_lab_outage_during_observation_is_intervention_not_participant_failure(
    sql_client,
    sql_factory,
    monkeypatch,
    tmp_path,
    live_lab,
):
    case = lab_case(
        sql_client,
        sql_factory,
        monkeypatch,
        tmp_path,
        live_lab,
        "Observation outage",
        rest={"resource-request.create", "resource-request.milestones"},
        objectives={"acknowledged": "Acknowledge within the TEST ONLY window"},
    )
    draft = milestone_draft(case)
    rule = {
        "objective_id": case.objectives["acknowledged"],
        "step_id": draft.milestones,
        "field": "acknowledged_on_time",
        "operator": "eq",
        "value": True,
    }
    run = start_run(
        case,
        draft.saved,
        draft.frozen,
        observations=[milestone_policy(draft, "acknowledged_on_time", 1, 5)],
        objectives=[rule],
    )
    created_request(case, draft, run)
    first = drive(case, run, lambda view: step(view, draft.milestones)["samples"] >= 1)
    assert step(first, draft.milestones)["state"] == "waiting"
    live_lab.process.terminate()
    live_lab.process.wait(timeout=10)
    held = drive(case, run, lambda view: view["state"] == "intervention")
    reading = step(held, draft.milestones)
    assert reading["state"] == "failed" and reading["samples"] == 2
    assert any(
        item["kind"] == "operation.failed" and item["step_id"] == draft.milestones
        for item in held["events"]
    )
    finding = held["findings"][0]
    assert finding["state"] == "indeterminate"
    assert finding["reason"] == "A declared optional evidence field is missing."
    stopped = run_control(case.client, run.path, "stop")
    assert stopped["state"] == "stopped"
    assert stopped["findings"][0]["state"] == "indeterminate"


def test_real_lab_occupancy_threshold_is_strict_and_detection_uses_the_committed_inject(
    sql_client,
    sql_factory,
    monkeypatch,
    tmp_path,
    live_lab,
):
    case = lab_case(
        sql_client,
        sql_factory,
        monkeypatch,
        tmp_path,
        live_lab,
        "Strict capacity threshold",
        sql=True,
        objectives={
            "equal": "Exactly 85 percent is not a breach",
            "above": "Above 85 percent is a breach",
            "detected": "Breach observed within 120 seconds of the committed inject",
        },
    )
    rid, shelter = live_lab.manifest["run_id"], live_lab.manifest["shelters"][0]
    assert shelter["capacity"] == 120
    read, equal, read_equal, above, read_above = (str(uuid4()) for _ in range(5))

    def reading(sid, label, after):
        return {
            "id": sid,
            "label": label,
            "kind": "operation",
            "depends_on": [after] if after else [],
            "binding": case.bound("sql", "shelter.occupancy.read", "2"),
            "parameters": {"run_id": rid, "shelter_id": shelter["id"]},
        }

    def injection(sid, label, occupancy, source):
        return {
            "id": sid,
            "label": label,
            "kind": "operation",
            "depends_on": [source],
            "binding": case.bound("sql", "shelter.occupancy.update", "2"),
            "parameters": {
                "run_id": rid,
                "shelter_id": shelter["id"],
                "occupancy": occupancy,
                "expected_version": {"source_step_id": source, "field": "record_version"},
            },
        }

    saved, frozen = save_draft(
        case,
        [
            reading(read, "Read SQL shelter", None),
            injection(equal, "Inject exactly 85 percent", 102, read),
            reading(read_equal, "Read exactly 85 percent", equal),
            injection(above, "Inject just above 85 percent", 103, read_equal),
            reading(read_above, "Read just above 85 percent", above),
        ],
    )
    breach = {"field": "occupancy_percent", "operator": "gt", "value": 85}
    run = start_run(
        case,
        saved,
        frozen,
        objectives=[
            {"objective_id": case.objectives["equal"], "step_id": read_equal, **breach},
            {"objective_id": case.objectives["above"], "step_id": read_above, **breach},
            {
                "objective_id": case.objectives["detected"],
                "step_id": read_above,
                **breach,
                "anchor_step_id": above,
                "anchor_field": "committed_at",
                "within_seconds": 120,
            },
        ],
    )
    final = drive(case, run, completed)
    assert step(final, read_equal)["result"]["occupancy_percent"] == 85.0
    assert step(final, read_above)["result"]["occupancy_percent"] == pytest.approx(10300 / 120)
    assert (
        step(final, read_above)["result"]["durable_event_id"]
        == step(final, above)["result"]["durable_event_id"]
    )
    assert verdicts(case, final) == {"equal": "unmet", "above": "met", "detected": "met"}
