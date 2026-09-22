"""Explicit test-only model fixture; never imported by the application."""

import asyncio
import os

from gametheory import worker
from gametheory.domain import ProposalContent, ScenarioContent


async def fixture_model(
    context: str, prompt: str, history: list[dict[str, str]]
) -> ProposalContent:
    await asyncio.sleep(float(os.environ.get("GT_TEST_MODEL_DELAY", "0")))
    content = ScenarioContent.model_validate_json(context)
    content.title = "Restart-safe fixture proposal"
    return ProposalContent(
        summary="Explicit test fixture, not a live model result", content=content
    )


worker.generate_proposal = fixture_model
worker.main()
