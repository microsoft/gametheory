import base64
import os
import socket
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
from azure.storage.blob import BlobServiceClient
from fastapi import HTTPException

from gametheory.assets import blob_service, put_blob, read_blob
from gametheory.config import Settings


def test_real_azurite_blob_round_trip_is_private_and_immutable(tmp_path, monkeypatch):
    executable = Path(__file__).parents[2] / "node_modules/.bin/azurite-blob"
    if not executable.exists():
        pytest.skip("Run npm ci to install the local Blob emulator")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    key = base64.b64encode(os.urandom(32)).decode()
    env = {**os.environ, "AZURITE_ACCOUNTS": f"gametheory:{key}"}
    log = (tmp_path / "azurite.log").open("w")
    process = subprocess.Popen(
        [
            str(executable),
            "--blobHost",
            "127.0.0.1",
            "--blobPort",
            str(port),
            "--location",
            str(tmp_path / "blobs"),
            "--silent",
            "--skipApiVersionCheck",
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    connection = f"DefaultEndpointsProtocol=http;AccountName=gametheory;AccountKey={key};BlobEndpoint=http://127.0.0.1:{port}/gametheory;"
    settings = Settings(
        _env_file=None, blob_connection_string=connection, blob_container="test-assets"
    )
    monkeypatch.setattr("gametheory.assets.get_settings", lambda: settings)
    blob_service.cache_clear()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail("Azurite exited before accepting connections")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("Azurite did not become responsive")
        client = blob_service()
        client.create_container(settings.blob_container)
        blob_key = f"workspace/{uuid4()}"
        put_blob(blob_key, b"verified immutable data", "text/plain")
        assert read_blob(blob_key) == b"verified immutable data"
        assert read_blob(blob_key, max_bytes=22) == b"verified immutable data"
        assert read_blob(blob_key, max_bytes=4) == b"verif"
        with pytest.raises(HTTPException) as error:
            put_blob(blob_key, b"replacement", "text/plain")
        assert error.value.status_code == 503
        assert read_blob(blob_key) == b"verified immutable data"
        assert (
            client.get_container_client(settings.blob_container)
            .get_container_properties()
            .public_access
            is None
        )
        anonymous = BlobServiceClient(f"http://127.0.0.1:{port}/gametheory")
        with pytest.raises(Exception) as denied:
            anonymous.get_blob_client(settings.blob_container, blob_key).download_blob().readall()
        assert getattr(denied.value, "status_code", None) in {403, 404}
        anonymous.close()
        client.close()
    finally:
        blob_service.cache_clear()
        process.terminate()
        process.wait(timeout=10)
        log.close()
