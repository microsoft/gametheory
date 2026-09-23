import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from gametheory.domain import ScenarioContent
from gametheory.persistence import (
    Audit,
    DispatchIntent,
    PlanningRequest,
    PreparationBoard,
    PreparationPreview,
    RunSetupDispatchIntent,
    RunSetupRequest,
    Scenario,
    Workspace,
)
from gametheory.run_setup import RunCheckContext, RunCheckSuggestion

pytestmark = pytest.mark.integration


def test_worker_crash_resumes_one_persisted_proposal(sql_client, sql_factory, tmp_path):
    endpoint = os.environ.get("GT_TEST_SCHEDULER_ENDPOINT")
    if not endpoint:
        pytest.skip(
            "GT_TEST_SCHEDULER_ENDPOINT not configured; worker-restart integration not executed"
        )
    _, actor = sql_client
    pid = str(uuid4())
    with sql_factory.begin() as db:
        workspace = Workspace(organization_id=actor.tenant, name="Scheduler integration")
        db.add(workspace)
        db.flush()
        scenario = Scenario(
            workspace_id=workspace.id,
            content=ScenarioContent(title="Restart test").model_dump_json(),
        )
        db.add(scenario)
        db.flush()
        db.add(
            PlanningRequest(
                id=pid,
                scenario_id=scenario.id,
                actor=actor.object_id,
                base_version=1,
                prompt="Test-only restart fixture",
                context=scenario.content,
            )
        )
        db.flush()
        db.add(DispatchIntent(request_id=pid))
    env = {
        **os.environ,
        "GT_SQL_URL": os.environ["GT_TEST_SQL_URL"],
        "GT_TENANT_ID": actor.tenant,
        "GT_PLANNING_ENABLED": "true",
        "GT_SCHEDULER_ENDPOINT": endpoint,
        "GT_SCHEDULER_EMULATOR": os.environ.get("GT_TEST_SCHEDULER_EMULATOR", "true"),
        "GT_SCHEDULER_TASKHUB": os.environ.get("GT_TEST_SCHEDULER_TASKHUB", "default"),
        "GT_FOUNDRY_PROJECT_ENDPOINT": "https://fixture.invalid/api/projects/test",
        "GT_MODEL_DEPLOYMENT": "explicit-test-fixture",
        "GT_TEST_MODEL_DELAY": "120",
    }
    log = (tmp_path / "worker.log").open("w")
    command = [sys.executable, str(Path(__file__).with_name("emulator_worker.py"))]
    process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)

    def wait_for(status, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with sql_factory() as db:
                pending = db.get(PlanningRequest, pid)
                if pending.status == status:
                    return
                if pending.status == "failed":
                    pytest.fail(f"Fixture workflow failed: {pending.error}")
            if process.poll() is not None:
                pytest.fail("Fixture worker exited unexpectedly; inspect the worker log")
            time.sleep(0.5)
        pytest.fail(f"Fixture request did not reach {status}; inspect the worker log")

    try:
        wait_for("running")
        process.kill()
        process.wait(timeout=10)
        process = subprocess.Popen(
            command, env={**env, "GT_TEST_MODEL_DELAY": "0"}, stdout=log, stderr=subprocess.STDOUT
        )
        wait_for("proposed")
        with sql_factory() as db:
            count = db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(
                    Audit.resource_id == pid,
                    Audit.operation == "planning.proposed",
                )
            )
            assert count == 1
            assert db.get(PlanningRequest, pid).proposal is not None
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()


def test_worker_crash_resumes_one_persisted_run_check_suggestion(sql_client, sql_factory, tmp_path):
    endpoint = os.environ.get("GT_TEST_SCHEDULER_ENDPOINT")
    if not endpoint:
        pytest.skip(
            "GT_TEST_SCHEDULER_ENDPOINT not configured; run-check restart integration not executed"
        )
    _, actor = sql_client
    rid = str(uuid4())
    with sql_factory.begin() as db:
        workspace = Workspace(organization_id=actor.tenant, name="Run-check scheduler integration")
        db.add(workspace)
        db.flush()
        # Minimal parent rows for the foreign keys; the worker reads only the stored context.
        board = PreparationBoard(workspace_id=workspace.id, name="Restart test", draft="{}")
        db.add(board)
        db.flush()
        frozen = PreparationPreview(
            board_id=board.id,
            board_version=1,
            sequence=1,
            digest="0" * 64,
            manifest="{}",
            findings="[]",
            created_by=actor.object_id,
            correlation_id=str(uuid4()),
        )
        db.add(frozen)
        db.flush()
        db.add(
            RunSetupRequest(
                id=rid,
                board_id=board.id,
                workspace_id=workspace.id,
                preview_id=frozen.id,
                preview_digest=frozen.digest,
                actor=actor.object_id,
                prompt="Test-only restart fixture",
                context=RunCheckContext(
                    window_seconds=3600, steps=[], objectives=[], recovery_operations=[]
                ).model_dump_json(),
                status="queued",
            )
        )
        db.flush()
        db.add(RunSetupDispatchIntent(request_id=rid))
    env = {
        **os.environ,
        "GT_SQL_URL": os.environ["GT_TEST_SQL_URL"],
        "GT_TENANT_ID": actor.tenant,
        "GT_PLANNING_ENABLED": "true",
        "GT_RUN_ASSISTANT_ENABLED": "true",
        "GT_SCHEDULER_ENDPOINT": endpoint,
        "GT_SCHEDULER_EMULATOR": os.environ.get("GT_TEST_SCHEDULER_EMULATOR", "true"),
        "GT_SCHEDULER_TASKHUB": os.environ.get("GT_TEST_SCHEDULER_TASKHUB", "default"),
        "GT_FOUNDRY_PROJECT_ENDPOINT": "https://fixture.invalid/api/projects/test",
        "GT_MODEL_DEPLOYMENT": "explicit-test-fixture",
        "GT_TEST_MODEL_DELAY": "120",
    }
    log = (tmp_path / "run-check-worker.log").open("w")
    command = [sys.executable, str(Path(__file__).with_name("emulator_worker.py"))]
    process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)

    def wait_for(status, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with sql_factory() as db:
                pending = db.get(RunSetupRequest, rid)
                if pending.status == status:
                    return
                if pending.status == "failed":
                    pytest.fail(f"Fixture run-check workflow failed: {pending.error}")
            if process.poll() is not None:
                pytest.fail("Fixture worker exited unexpectedly; inspect the worker log")
            time.sleep(0.5)
        pytest.fail(f"Fixture run-check request did not reach {status}; inspect the worker log")

    try:
        wait_for("running")
        process.kill()
        process.wait(timeout=10)
        process = subprocess.Popen(
            command, env={**env, "GT_TEST_MODEL_DELAY": "0"}, stdout=log, stderr=subprocess.STDOUT
        )
        wait_for("proposed")
        with sql_factory() as db:
            count = db.scalar(
                select(func.count())
                .select_from(Audit)
                .where(Audit.resource_id == rid, Audit.operation == "run_setup.suggested")
            )
            assert count == 1
            stored = RunCheckSuggestion.model_validate_json(db.get(RunSetupRequest, rid).suggestion)
            assert stored.questions == ["Fixture question about 0 pinned steps"]
            assert db.get(RunSetupDispatchIntent, rid).state == "scheduled"
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
