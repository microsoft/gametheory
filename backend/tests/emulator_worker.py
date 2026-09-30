"""Explicit test-only model fixture; never imported by the application."""

import asyncio
import os

from gametheory import worker
from gametheory.domain import ProposalContent, ScenarioContent
from gametheory.run_setup import RunCheckContext, RunCheckSuggestion


async def fixture_model(
    context: str, prompt: str, history: list[dict[str, str]]
) -> ProposalContent:
    await asyncio.sleep(float(os.environ.get("GT_TEST_MODEL_DELAY", "0")))
    content = ScenarioContent.model_validate_json(context)
    content.title = "Restart-safe fixture proposal"
    return ProposalContent(
        summary="Explicit test fixture, not a live model result", content=content
    )


async def fixture_run_checks(
    context: str, prompt: str, history: list[dict[str, str]]
) -> RunCheckSuggestion:
    await asyncio.sleep(float(os.environ.get("GT_TEST_MODEL_DELAY", "0")))
    steps = RunCheckContext.model_validate_json(context).steps
    return RunCheckSuggestion(
        summary="Explicit test fixture, not a live model result",
        questions=[f"Fixture question about {len(steps)} pinned steps"],
    )


worker.generate_proposal = fixture_model
worker.generate_run_checks = fixture_run_checks
worker.main()
