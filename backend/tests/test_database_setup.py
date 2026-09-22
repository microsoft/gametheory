from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from gametheory.database_setup import (
    API_PERMISSIONS,
    WORKER_PERMISSIONS,
    provision_runtime_users,
)


def test_runtime_grants_use_client_sid_and_keep_immutable_tables_read_insert_only():
    db = MagicMock()
    db.execute.return_value.first.return_value = None
    client = UUID("00112233-4455-6677-8899-aabbccddeeff")
    provision_runtime_users(db, client, uuid4())
    statements = [str(call.args[0]) for call in db.execute.call_args_list]
    assert (
        "CREATE USER [gametheory_api] WITH SID = 0x33221100554477668899aabbccddeeff, TYPE = E"
        in statements
    )
    assert API_PERMISSIONS["administrators"] == "SELECT"
    assert API_PERMISSIONS["revisions"] == API_PERMISSIONS["audit"] == "SELECT, INSERT"
    assert WORKER_PERMISSIONS["audit"] == "INSERT"
    assert WORKER_PERMISSIONS["scenarios"] == "SELECT"
    for table in (
        "connection_configurations",
        "configuration_withdrawals",
        "workspace_approver_grants",
        "approver_grant_revocations",
        "board_origins",
        "board_contributors",
        "board_previews",
        "board_approvals",
        "approval_revocations",
    ):
        assert API_PERMISSIONS[table] == "SELECT, INSERT"
        assert table not in WORKER_PERMISSIONS
    assert API_PERMISSIONS["boards"] == "SELECT, INSERT, UPDATE"
    assert "preparation_collections" not in API_PERMISSIONS
    assert "preparation_collections" not in WORKER_PERMISSIONS
    assert "boards" not in WORKER_PERMISSIONS
    assert not any("ALTER" in statement or "CONTROL" in statement for statement in statements)


def test_existing_or_shared_identity_cannot_be_repurposed():
    db = MagicMock()
    client = uuid4()
    with pytest.raises(ValueError, match="separate"):
        provision_runtime_users(db, client, client)
    db.execute.return_value.first.return_value = (uuid4().bytes_le, "E")
    with pytest.raises(ValueError, match="different identity"):
        provision_runtime_users(db, client, uuid4())
