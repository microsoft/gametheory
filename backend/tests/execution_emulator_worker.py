"""Test-only provider fixture for the real SQL/Scheduler worker-restart gate."""

import os

from sqlalchemy.engine import make_url

from gametheory import execution_worker
from gametheory.execution_adapters import AdapterResult

if (
    not os.environ.get("GT_TEST_EXECUTION_SCHEDULER_ENDPOINT")
    or not (make_url(os.environ["GT_TEST_SQL_URL"]).database or "").startswith("gametheory_test")
    or os.environ["GT_SQL_URL"] != os.environ["GT_TEST_SQL_URL"]
):
    raise SystemExit("Only an explicitly configured disposable execution test is supported")


def fixture_read(binding, operation, _parameters, _key):
    if (
        operation.key != "record.read"
        or operation.effect != "read"
        or binding.resource_id != "record-service/example"
    ):
        raise ValueError("Unexpected operation in the test-only provider fixture")
    return AdapterResult(outcome="succeeded", values={"quantity": 86})


execution_worker.invoke = fixture_read
execution_worker.main()
