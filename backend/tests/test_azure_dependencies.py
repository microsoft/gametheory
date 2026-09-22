import asyncio
import ipaddress
import os
import socket
from collections.abc import Generator
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pytest
from azure.identity import DefaultAzureCredential
from durabletask import task
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.azuremanaged.worker import DurableTaskSchedulerWorker
from durabletask.client import OrchestrationStatus

from gametheory.assets import blob_service, put_blob, read_blob
from gametheory.config import get_settings
from gametheory.domain import ScenarioContent
from gametheory.planning import generate_proposal

pytestmark = pytest.mark.skipif(
    os.environ.get("GT_TEST_AZURE_DEPENDENCIES") != "true",
    reason="Explicit private-network Azure dependency probe was not requested",
)


def dependency_probe_v1(
    ctx: task.OrchestrationContext, value: str
) -> Generator[task.Task[Any], Any, str]:
    result = yield ctx.call_activity(dependency_activity_v1, input=value)
    return str(result)


def dependency_activity_v1(_ctx: task.ActivityContext, value: str) -> str:
    return value


def test_private_dns_for_available_services():
    settings = get_settings()
    for endpoint in (
        settings.blob_url,
        settings.scheduler_endpoint,
        settings.foundry_project_endpoint,
    ):
        hostname = urlparse(endpoint).hostname
        assert hostname
        addresses = {
            record[4][0] for record in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        }
        assert addresses
        assert all(ipaddress.ip_address(address).is_private for address in addresses)


def test_real_foundry_produces_a_valid_authoring_proposal():
    content = ScenarioContent(title="Deployment validation")
    proposal = asyncio.run(
        generate_proposal(
            content.model_dump_json(),
            "This is synthetic deployment validation. Add one objective to practice a tabletop "
            "communications exercise. Preserve the title. Do not add connections or assets.",
            [],
        )
    )
    assert proposal.content.title == content.title
    assert proposal.content.objectives
    assert not proposal.content.asset_ids


def test_private_blob_round_trip():
    key = f"deployment-validation/{uuid4()}"
    payload = b"Synthetic Game Theory deployment validation."
    put_blob(key, payload, "text/plain")
    try:
        assert read_blob(key, max_bytes=len(payload)) == payload
    finally:
        blob_service().get_blob_client(get_settings().blob_container, key).delete_blob()


def test_managed_scheduler_runs_an_activity_with_workload_identity():
    settings = get_settings()
    assert settings.scheduler_taskhub == "validation", "Use the isolated validation task hub"
    with DefaultAzureCredential() as credential:
        client = DurableTaskSchedulerClient(
            host_address=settings.scheduler_endpoint,
            taskhub=settings.scheduler_taskhub,
            token_credential=credential,
        )
        worker = DurableTaskSchedulerWorker(
            host_address=settings.scheduler_endpoint,
            taskhub=settings.scheduler_taskhub,
            token_credential=credential,
        )
        orchestrator: task.Orchestrator[str, str] = dependency_probe_v1
        worker.add_orchestrator(orchestrator)
        worker.add_activity(dependency_activity_v1)
        expected = str(uuid4())
        try:
            with worker:
                worker.start()
                instance = client.schedule_new_orchestration(
                    orchestrator, input=expected, instance_id=f"validation-{expected}"
                )
                result = client.wait_for_orchestration_completion(
                    instance, timeout=90, fetch_payloads=True
                )
                assert result is not None
                assert result.runtime_status == OrchestrationStatus.COMPLETED
                assert result.serialized_output == f'"{expected}"'
        finally:
            client.close()
