"""Explicitly authorized smoke test; creates synthetic records in the deployed app."""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("GT_LIVE_API_URL") or not os.environ.get("GT_LIVE_API_TOKEN"),
    reason="A deployed URL and explicitly authorized user token are required",
)


def test_persisted_authoring_and_reviewed_real_planning():
    url = os.environ["GT_LIVE_API_URL"].rstrip("/")
    assert url.startswith("https://")
    with httpx.Client(
        base_url=url,
        headers={"Authorization": "Bearer " + os.environ["GT_LIVE_API_TOKEN"]},
        timeout=60,
    ) as api:

        def call(method, path, status=200, **kwargs):
            response = api.request(method, "/api" + path, **kwargs)
            assert response.status_code == status, (path, response.status_code, response.text)
            return response

        config = call("GET", "/config").json()
        assert config["capabilities"]["authoring"]
        assert config["capabilities"]["planning"]
        assert not config["capabilities"]["execution"]
        call("GET", "/me", 401, headers={"Authorization": "Bearer invalid-validation-token"})
        actor = call("GET", "/me").json()
        assert actor["object_id"] == os.environ["GT_LIVE_API_EXPECTED_USER"]
        assert actor["organization_admin"]
        workspace = call("POST", "/workspaces", 201, json={"name": "Deployment validation"}).json()
        base = f"/workspaces/{workspace['id']}"
        scenario = call(
            "POST", base + "/scenarios", 201, json={"name": "Synthetic communications exercise"}
        ).json()
        path = base + "/scenarios/" + scenario["id"]
        environments = call("GET", "/environments").json()
        environment = next(item for item in environments if item["name"] == "Test")
        connection = call(
            "POST",
            base + "/connections",
            201,
            json={
                "name": "Validation inventory - no external access",
                "kind": "sql",
                "scope": "workspace",
                "environment_id": environment["id"],
                "description": "Synthetic metadata only. No organizational system is connected.",
            },
        ).json()
        payload = b"exercise,status\nsynthetic,validation\n"
        asset = call(
            "POST",
            base + "/assets",
            201,
            files={"file": ("validation.csv", payload, "text/csv")},
        ).json()
        assert call("GET", base + f"/assets/{asset['id']}/content").content == payload
        replacement = call(
            "POST",
            base + f"/assets?previous_id={asset['id']}",
            201,
            files={"file": ("validation.csv", b"exercise,status\nsynthetic,updated\n", "text/csv")},
        ).json()
        assert replacement["previous_id"] == asset["id"]
        assert call("GET", base + f"/assets/{asset['id']}/content").content == payload
        content = scenario["content"]
        content["asset_ids"] = [asset["id"]]
        content["document"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Synthetic deployment validation. No external actions are authorized.",
                        }
                    ],
                },
                {"type": "reference", "attrs": {"kind": "asset", "targetId": asset["id"]}},
            ],
        }
        content["objectives"] = [
            {
                "id": str(uuid4()),
                "title": "Coordinate a tabletop communications response",
                "criterion": "Record a shared operating picture and timestamped acknowledgements.",
            }
        ]
        content["nodes"] = [
            {
                "id": str(uuid4()),
                "label": "Describe initial exercise conditions",
                "kind": "action",
                "detail": "Planning metadata only; not an executable organizational action.",
                "position": {"x": 100, "y": 100},
                "connection_id": connection["id"],
                "environment_id": environment["id"],
            }
        ]
        saved = call("PUT", path, headers={"If-Match": '"1"'}, json=content)
        assert saved.headers["etag"] == '"2"'
        content = saved.json()["content"]
        call("PUT", path, 409, headers={"If-Match": '"1"'}, json=content)
        call(
            "POST",
            path + "/comments",
            201,
            json={"body": "Live deployment validation record.", "base_version": 2},
        )
        call("POST", path + "/revisions", 201, headers={"If-Match": '"2"'})
        request_id = str(uuid4())
        request_body = {
            "request_id": request_id,
            "base_version": 2,
            "prompt": "This is synthetic deployment validation. Refine the narrative to explain "
            "the existing communications objective. Preserve the title, all object identifiers, "
            "asset references, and connection/environment bindings. Do not add any external actions.",
        }
        call("POST", path + "/planning", 202, json=request_body)
        call("POST", path + "/planning", 202, json=request_body)
        deadline = time.monotonic() + 300
        while True:
            requests = call("GET", path + "/planning").json()
            matches = [item for item in requests if item["id"] == request_id]
            assert len(matches) == 1
            pending = matches[0]
            assert pending["status"] != "failed", pending.get("error")
            if pending["status"] == "proposed":
                break
            assert time.monotonic() < deadline, f"Planning remained {pending['status']}"
            time.sleep(3)
        assert call("GET", path).json()["version"] == 2
        applied = call(
            "POST", path + f"/planning/{request_id}/apply", headers={"If-Match": '"2"'}
        ).json()
        assert applied["scenario"]["version"] == 3
        assert call("GET", path).json()["version"] == 3
        revisions = call("GET", path + "/revisions").json()
        assert next(item for item in revisions if item["version"] == 2)["content"] == content
        assert call("GET", path + "/comments").json()
        artifact = os.environ.get("GT_LIVE_API_ARTIFACT")
        if artifact:
            Path(artifact).write_text(
                json.dumps(
                    {
                        "workspace_id": workspace["id"],
                        "scenario_id": scenario["id"],
                        "request_id": request_id,
                        "asset_id": asset["id"],
                        "version": 3,
                        "scenario_url": url + f"/w/{workspace['id']}/s/{scenario['id']}",
                    },
                    indent=2,
                )
                + "\n"
            )
