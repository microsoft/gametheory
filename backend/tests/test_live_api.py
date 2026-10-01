"""Explicitly authorized smoke tests; they create synthetic records in the deployed app.

Tokens arrive only through environment variables and are never printed or written to disk.
See docs/live-acceptance.md for how each optional input is obtained.
"""

import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import jwt
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("GT_LIVE_API_URL"),
    reason="A deployed studio URL is required",
)

PLANNING_PROMPT = (
    "This is synthetic deployment validation. Refine the narrative to explain "
    "the existing communications objective. Preserve the title, all object identifiers, "
    "asset references, and connection/environment bindings. Do not add any external actions."
)
ASSET_PAYLOAD = b"exercise,status\nsynthetic,validation\n"
# Distinctive fragments that must never appear in application logs.
LOG_MARKERS = [
    "Refine the narrative to explain the existing communications",
    "synthetic,validation",
]


def live_url() -> str:
    url = os.environ["GT_LIVE_API_URL"].rstrip("/")
    assert url.startswith("https://")
    return url


def owner_token() -> str:
    token = os.environ.get("GT_LIVE_API_TOKEN")
    if not token:
        pytest.skip("An explicitly authorized owner token (GT_LIVE_API_TOKEN) is required")
    return token


def api_client(token: str | None = None) -> httpx.Client:
    headers = {"Authorization": "Bearer " + token} if token else {}
    return httpx.Client(base_url=live_url(), headers=headers, timeout=60)


def expect(
    api: httpx.Client, method: str, path: str, status: int = 200, **kwargs: Any
) -> httpx.Response:
    response = api.request(method, "/api" + path, **kwargs)
    assert response.status_code == status, (path, response.status_code, response.text)
    return response


def unverified_claims(token: str) -> dict[str, Any]:
    # Only confirms which negative case a supplied token represents; never trusted.
    claims: dict[str, Any] = jwt.decode(token, options={"verify_signature": False})
    return claims


def test_persisted_authoring_and_reviewed_real_planning():
    token = owner_token()
    url = os.environ["GT_LIVE_API_URL"].rstrip("/")
    assert url.startswith("https://")
    with httpx.Client(
        base_url=url,
        headers={"Authorization": "Bearer " + token},
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
        payload = ASSET_PAYLOAD
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
            "prompt": PLANNING_PROMPT,
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
                        "planning_instance": f"planning-{request_id}",
                        "asset_id": asset["id"],
                        "version": 3,
                        "scenario_url": url + f"/w/{workspace['id']}/s/{scenario['id']}",
                        "log_markers": LOG_MARKERS,
                    },
                    indent=2,
                )
                + "\n"
            )


def test_live_rejects_missing_and_malformed_tokens():
    with api_client() as anonymous:
        for headers in ({}, {"Authorization": "Bearer not-a-token"}):
            response = expect(anonymous, "GET", "/me", 401, headers=headers)
            assert response.headers.get("www-authenticate") == "Bearer"


# Each variable holds a real token that the API must refuse for one specific reason.
DENIED_TOKENS = {
    "GT_LIVE_API_WRONG_AUDIENCE_TOKEN": "same tenant, another resource",
    "GT_LIVE_API_WRONG_TENANT_TOKEN": "issued by another tenant",
    "GT_LIVE_API_EXPIRED_TOKEN": "a studio token whose exp has passed",
}


@pytest.mark.parametrize("variable", list(DENIED_TOKENS))
def test_live_token_denials(variable):
    token = os.environ.get(variable)
    if not token:
        pytest.skip(f"{variable} is not set ({DENIED_TOKENS[variable]})")
    with api_client() as anonymous:
        auth = expect(anonymous, "GET", "/config").json()["auth"]
        tenant = auth["authority"].rstrip("/").rsplit("/", 1)[-1]
        resource = auth["scope"].rsplit("/", 1)[0]
        audiences = {resource, resource.removeprefix("api://")}
        claims = unverified_claims(token)
        if variable == "GT_LIVE_API_WRONG_AUDIENCE_TOKEN":
            assert claims["tid"] == tenant and claims["aud"] not in audiences
        elif variable == "GT_LIVE_API_WRONG_TENANT_TOKEN":
            assert claims["tid"] != tenant
        else:
            assert claims["tid"] == tenant and claims["aud"] in audiences
            assert claims["exp"] < time.time(), "The supplied token has not expired yet"
        response = expect(
            anonymous, "GET", "/me", 401, headers={"Authorization": "Bearer " + token}
        )
        assert response.headers.get("www-authenticate") == "Bearer"


def test_live_workspace_isolation_and_revocation():
    member_token = os.environ.get("GT_LIVE_API_MEMBER_TOKEN")
    if not member_token:
        pytest.skip("GT_LIVE_API_MEMBER_TOKEN is not set (a non-administrator member)")
    with api_client(owner_token()) as owner, api_client(member_token) as member:
        me = expect(member, "GET", "/me").json()
        assert not me["organization_admin"], "Use a member who is not an administrator"
        if os.environ.get("GT_LIVE_API_MEMBER_USER"):
            assert me["object_id"] == os.environ["GT_LIVE_API_MEMBER_USER"]
        workspace = expect(
            owner, "POST", "/workspaces", 201, json={"name": "Isolation validation"}
        ).json()
        base = f"/workspaces/{workspace['id']}"
        scenario = expect(
            owner, "POST", base + "/scenarios", 201, json={"name": "Synthetic isolation check"}
        ).json()
        path = f"{base}/scenarios/{scenario['id']}"

        def listed():
            workspaces = expect(member, "GET", "/workspaces").json()
            return any(item["id"] == workspace["id"] for item in workspaces)

        assert not listed()
        expect(member, "GET", path, 404)
        expect(member, "GET", base + "/members", 404)
        expect(
            owner, "PUT", base + "/members", json={"object_id": me["object_id"], "role": "viewer"}
        )
        assert listed()
        content = expect(member, "GET", path).json()["content"]
        expect(member, "PUT", path, 403, headers={"If-Match": '"1"'}, json=content)
        expect(owner, "DELETE", f"{base}/members/{me['object_id']}", 204)
        assert not listed()
        expect(member, "GET", path, 404)
        assert expect(owner, "GET", path).json()["version"] == 1


def test_live_records_persist_after_restart():
    artifact = os.environ.get("GT_LIVE_API_VERIFY_ARTIFACT")
    if not artifact:
        pytest.skip("GT_LIVE_API_VERIFY_ARTIFACT is not set (an artifact from an earlier run)")
    record = json.loads(Path(artifact).read_text())
    with api_client(owner_token()) as owner:
        base = f"/workspaces/{record['workspace_id']}"
        path = f"{base}/scenarios/{record['scenario_id']}"
        scenario = expect(owner, "GET", path)
        assert scenario.json()["version"] == record["version"]
        assert scenario.headers["etag"] == f'"{record["version"]}"'
        revisions = expect(owner, "GET", path + "/revisions").json()
        assert any(item["version"] == 2 for item in revisions)
        content = expect(owner, "GET", f"{base}/assets/{record['asset_id']}/content").content
        assert content == ASSET_PAYLOAD
        requests = expect(owner, "GET", path + "/planning").json()
        request = next(item for item in requests if item["id"] == record["request_id"])
        assert request["status"] == "applied" and request["proposal"]
