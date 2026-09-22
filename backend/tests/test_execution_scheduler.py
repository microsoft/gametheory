import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from execution_support import execution_case
from sqlalchemy import func, select
from test_preparation_sql import require

from gametheory.persistence import ExerciseRunState, RunEvent, RunStep

pytestmark = pytest.mark.integration


def test_execution_worker_resumes_the_same_durable_wait(
    sql_client, sql_factory, monkeypatch, tmp_path
):
    endpoint = os.environ.get("GT_TEST_EXECUTION_SCHEDULER_ENDPOINT")
    if not endpoint:
        pytest.skip(
            "Isolated exercise Scheduler not configured; durable run restart gate not executed"
        )
    wait_id = str(uuid4())

    def steps(original):
        return [
            {"id": wait_id, "label": "Persisted wait", "kind": "wait", "wait_seconds": 20},
            {**original[0], "depends_on": [wait_id]},
        ]

    case = execution_case(sql_client, sql_factory, monkeypatch, tmp_path, steps=steps)
    require(case.control("authorize"))
    require(case.control("start"))
    env = {
        **os.environ,
        "GT_SQL_URL": os.environ["GT_TEST_SQL_URL"],
        "GT_TENANT_ID": case.actor.tenant,
        "GT_PLANNING_ENABLED": "false",
        "GT_EXECUTION_ENABLED": "true",
        "GT_EXECUTION_TASKHUB": "default",
        "GT_EXECUTION_BINDINGS_FILE": str(case.bindings_path),
        "GT_SCHEDULER_ENDPOINT": endpoint,
        "GT_SCHEDULER_EMULATOR": "true",
    }
    command = [sys.executable, str(Path(__file__).with_name("execution_emulator_worker.py"))]
    log = (tmp_path / "execution-worker.log").open("w")
    process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)

    def wait_for(status):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            with sql_factory() as db:
                state = db.get(ExerciseRunState, case.run["id"])
                if state.state == status:
                    return
                if state.state == "intervention":
                    pytest.fail(f"Fixture run requires intervention: {state.reason}")
            if process.poll() is not None:
                pytest.fail("Fixture executor exited; inspect execution-worker.log")
            time.sleep(0.25)
        pytest.fail(f"Fixture run did not reach {status}; inspect execution-worker.log")

    try:
        wait_for("waiting")
        with sql_factory() as db:
            started_at = db.get(RunStep, (case.run["id"], "exercise", wait_id)).started_at
        process.kill()
        process.wait(timeout=10)
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        wait_for("completed")
        with sql_factory() as db:
            assert db.get(RunStep, (case.run["id"], "exercise", wait_id)).started_at == started_at
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RunEvent)
                    .where(RunEvent.run_id == case.run["id"], RunEvent.kind == "wait.finished")
                )
                == 1
            )
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(RunEvent)
                    .where(
                        RunEvent.run_id == case.run["id"], RunEvent.kind == "operation.succeeded"
                    )
                )
                == 1
            )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log.close()
