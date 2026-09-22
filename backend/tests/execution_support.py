"""SQL-backed app setup. Runtime/provider readiness here is explicitly a test fixture."""

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from test_preparation_sql import bound_draft, configuration, preview, published_board, require

from gametheory import execution_adapters, execution_service, execution_worker
from gametheory.config import Settings
from gametheory.execution_adapters import AllowedOperation, TargetBinding
from gametheory.persistence import TargetReadiness, now
from gametheory.preparation import canonical_digest


def execution_case(
    sql_client, sql_factory, monkeypatch, tmp_path, classification="nonproduction", *, steps=None
):
    client, actor = sql_client
    wid, workspace, _, _, board, board_path = published_board(client, "Execution integration")
    _, _, config = configuration(client, workspace, classification=classification)
    policy_path = f"/api/admin/environment-policies/{config['environment_id']}"
    require(
        client.put(
            policy_path,
            headers={"If-Match": '"1"'},
            json={
                "classification": classification,
                "execution_enabled": True,
                "approval_required": classification == "production",
            },
        )
    )
    require(
        client.put(
            workspace + "/execution-grants",
            json={"object_id": actor.object_id, "capability": "operator"},
        )
    )
    draft = bound_draft(board, config)
    current = datetime.now(UTC)
    draft["window"] = {
        "starts_at": (current - timedelta(minutes=1)).isoformat(),
        "ends_at": (current + timedelta(hours=1)).isoformat(),
    }
    if steps:
        draft["steps"] = steps(draft["steps"])
    saved = require(client.put(board_path, json=draft, headers={"If-Match": '"1"'}))
    frozen = preview(client, board_path, saved["version"])
    operation = config["content"]["catalog"]["operations"][0]
    target = TargetBinding(
        configuration_id=config["id"],
        configuration_digest=config["digest"],
        environment_id=config["environment_id"],
        classification=classification,
        resource_id=config["content"]["resource_id"],
        endpoint=config["content"]["endpoint"],
        identity_ref=config["content"]["identity_ref"],
        client_id=uuid4(),
        token_scope="api://fixture/.default",
        operations=[AllowedOperation(digest=canonical_digest(operation), safe_replay=False)],
    )
    path = tmp_path / f"bindings-{wid}.json"
    path.write_text(json.dumps([target.model_dump(mode="json")]))
    settings = Settings(
        _env_file=None,
        sql_url=os.environ["GT_TEST_SQL_URL"],
        tenant_id=actor.tenant,
        scheduler_endpoint="http://127.0.0.1:8080",
        scheduler_emulator=True,
        execution_taskhub="default",
        execution_bindings_file=str(path),
        execution_enabled=True,
    )
    for module in (execution_adapters, execution_service, execution_worker):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(execution_worker, "session_factory", lambda: sql_factory)
    with sql_factory.begin() as db:
        db.add(
            TargetReadiness(
                configuration_id=config["id"],
                configuration_digest=config["digest"],
                binding_digest=target.digest,
                evidence_reference="fixture:sql-app-authorization-only",
                actor="sql:fixture",
                checked_at=now(),
                expires_at=now() + timedelta(hours=2),
            )
        )
    run = require(
        client.post(
            board_path + "/runs",
            headers={"If-Match": f'"{saved["version"]}"'},
            json={
                "preview_id": frozen["id"],
                "preview_digest": frozen["digest"],
            },
        ),
        201,
    )
    run_path = workspace + f"/runs/{run['id']}"

    def control(action):
        current_run = require(client.get(run_path))
        return client.post(
            run_path + "/controls",
            headers={"If-Match": f'"{current_run["version"]}"'},
            json={"action": action, "note": "Explicit isolated integration-test action"},
        )

    return SimpleNamespace(
        client=client,
        actor=actor,
        wid=wid,
        workspace=workspace,
        board=board,
        board_path=board_path,
        config=config,
        policy_path=policy_path,
        run=run,
        run_path=run_path,
        control=control,
        settings=settings,
        target=target,
        bindings_path=path,
    )
