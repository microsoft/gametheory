"""Fault scenarios against a disposable validation environment; see docs/live-acceptance.md.

Each scenario API is a separate deployment with one deliberate fault, so these tests never
touch a real studio. Tests that need the runner to change a scenario first run only in their
named phase (GT_SCENARIO_PHASE).
"""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("GT_SCENARIO_TOKEN"),
    reason="A token for the validation environment's API is required",
)

DATABASE_UNAVAILABLE = "Application database is unavailable. Retry after checking service health."
UPLOAD_FAILED = "Asset upload failed; no usable asset was published"
STORAGE_UNAVAILABLE = "Asset storage is unavailable"
DISPATCH_UNAVAILABLE = "Scheduler dispatch unavailable; retry is scheduled."
PLANNING_FAILED = "Planning failed after bounded retries. Submit a new request to retry."
PROMPT = "Synthetic fault validation. Add one objective for a tabletop communications exercise."
PAYLOAD = b"exercise,status\nsynthetic,fault-validation\n"


def scenario(variable: str) -> httpx.Client:
    url = os.environ.get(variable, "").rstrip("/")
    if not url:
        pytest.skip(f"{variable} is not set")
    assert url.startswith("https://")
    token = os.environ["GT_SCENARIO_TOKEN"]
    return httpx.Client(base_url=url, headers={"Authorization": "Bearer " + token}, timeout=60)


def phase(name: str) -> None:
    if os.environ.get("GT_SCENARIO_PHASE") != name:
        pytest.skip(f"Runs only in the {name} phase, after the runner changes the scenario")


def save(name: str, record: dict[str, Any]) -> None:
    directory = os.environ.get("GT_SCENARIO_ARTIFACTS")
    if directory:
        (Path(directory) / f"{name}.json").write_text(json.dumps(record, indent=2) + "\n")


def load(name: str) -> dict[str, Any]:
    directory = os.environ.get("GT_SCENARIO_ARTIFACTS")
    path = Path(directory) / f"{name}.json" if directory else None
    if path is None or not path.exists():
        pytest.skip(f"No {name} artifact from an earlier phase")
    record: dict[str, Any] = json.loads(path.read_text())
    return record


def expect(
    api: httpx.Client, method: str, path: str, status: int = 200, **kwargs: Any
) -> httpx.Response:
    response = api.request(method, "/api" + path, **kwargs)
    assert response.status_code == status, (path, response.status_code, response.text)
    return response


def new_scenario(api: httpx.Client) -> tuple[str, str]:
    workspace = expect(api, "POST", "/workspaces", 201, json={"name": "Fault validation"}).json()
    base = f"/workspaces/{workspace['id']}"
    created = expect(
        api, "POST", base + "/scenarios", 201, json={"name": "Synthetic fault check"}
    ).json()
    return base, f"{base}/scenarios/{created['id']}"


def request_proposal(api: httpx.Client, path: str) -> str:
    request_id = str(uuid4())
    body = {"request_id": request_id, "base_version": 1, "prompt": PROMPT}
    expect(api, "POST", path + "/planning", 202, json=body)
    return request_id


def planning_state(api: httpx.Client, path: str, request_id: str) -> dict[str, Any]:
    matches = [
        item for item in expect(api, "GET", path + "/planning").json() if item["id"] == request_id
    ]
    assert len(matches) == 1
    state: dict[str, Any] = matches[0]
    return state


def wait_for(
    api: httpx.Client,
    path: str,
    request_id: str,
    done: Callable[[dict[str, Any]], bool],
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        state = planning_state(api, path, request_id)
        if done(state):
            return state
        assert time.monotonic() < deadline, f"Request stayed {state['status']}: {state['error']}"
        time.sleep(5)


def finished(state: dict[str, Any]) -> bool:
    return state["status"] not in {"queued", "running"}


def test_sql_outage_returns_503_with_request_id():
    with scenario("GT_SCENARIO_SQL_OUTAGE_URL") as api:
        assert expect(api, "GET", "/health").json() == {"status": "alive"}
        assert expect(api, "GET", "/config").json()["capabilities"]["authoring"]
        request_ids = []
        for path in ("/me", "/workspaces"):
            response = expect(api, "GET", path, 503)
            body = response.json()
            assert body["detail"] == DATABASE_UNAVAILABLE
            assert body["request_id"] == response.headers["x-request-id"]
            request_ids.append(body["request_id"])
    save("sql-outage", {"request_ids": request_ids})


def test_blob_outage_rejects_uploads():
    with scenario("GT_SCENARIO_BLOB_OUTAGE_URL") as api:
        base, _ = new_scenario(api)
        upload = {"file": ("fault.csv", PAYLOAD, "text/csv")}
        response = expect(api, "POST", base + "/assets", 503, files=upload)
        assert response.json()["detail"] == UPLOAD_FAILED
        listed = expect(api, "GET", base + "/assets").json()
        assert listed and all(item["state"] != "ready" for item in listed)
        for item in listed:
            expect(api, "GET", f"{base}/assets/{item['id']}/content", 404)


def test_blob_seed_asset_while_healthy():
    phase("blob-healthy")
    with scenario("GT_SCENARIO_BLOB_OUTAGE_URL") as api:
        base, _ = new_scenario(api)
        upload = {"file": ("seed.csv", PAYLOAD, "text/csv")}
        asset = expect(api, "POST", base + "/assets", 201, files=upload).json()
        assert asset["state"] == "ready"
        assert expect(api, "GET", f"{base}/assets/{asset['id']}/content").content == PAYLOAD
    save("blob-seed", {"base": base, "asset_id": asset["id"]})


def test_blob_outage_fails_reads():
    phase("blob-read")
    seed = load("blob-seed")
    with scenario("GT_SCENARIO_BLOB_OUTAGE_URL") as api:
        content = f"{seed['base']}/assets/{seed['asset_id']}/content"
        response = expect(api, "GET", content, 503)
        assert response.json()["detail"] == STORAGE_UNAVAILABLE
        listed = expect(api, "GET", seed["base"] + "/assets").json()
        assert next(item for item in listed if item["id"] == seed["asset_id"])["state"] == "ready"


def test_scheduler_outage_keeps_request_queued():
    with scenario("GT_SCENARIO_SCHEDULER_OUTAGE_URL") as api:
        _, path = new_scenario(api)
        request_id = request_proposal(api, path)
        state = wait_for(
            api, path, request_id, lambda item: item["error"] == DISPATCH_UNAVAILABLE, 300
        )
        assert state["status"] == "queued" and state["proposal"] is None
        # The outage is visible and nothing success-shaped replaces the missing dispatch.
        time.sleep(30)
        state = planning_state(api, path, request_id)
        assert state["status"] == "queued" and state["proposal"] is None
    save("scheduler-outage", {"path": path, "request_id": request_id})


def test_scheduler_recovery_completes_queued_request():
    phase("scheduler-recovery")
    pending = load("scheduler-outage")
    with scenario("GT_SCENARIO_SCHEDULER_OUTAGE_URL") as api:
        state = wait_for(api, pending["path"], pending["request_id"], finished, 900)
        assert state["status"] == "proposed", state["error"]
        assert state["proposal"] and state["error"] is None
        requests = expect(api, "GET", pending["path"] + "/planning").json()
        assert [item["id"] for item in requests] == [pending["request_id"]]


def test_model_denial_fails_after_bounded_retries():
    with scenario("GT_SCENARIO_MODEL_DENIED_URL") as api:
        _, path = new_scenario(api)
        request_id = request_proposal(api, path)
        state = wait_for(api, path, request_id, finished, 1200)
        assert state["status"] == "failed"
        assert state["error"] == PLANNING_FAILED
        assert state["proposal"] is None
    save("model-denied", {"path": path, "request_id": request_id})
