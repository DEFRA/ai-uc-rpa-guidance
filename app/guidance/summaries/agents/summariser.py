"""Summariser agent: says what a guidance document is about and is used for."""

import logging

import pydantic_ai

from app.guidance.summaries import models

logger = logging.getLogger(__name__)

summariser_agent = pydantic_ai.Agent(
    deps_type=models.SummaryDependencies,
    output_type=models.SummaryOutput,
)


@summariser_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.SummaryDependencies],
) -> str:
    """Compose instructions: summariser prompt followed by the document."""
    logger.info("[Summary] Summariser agent: loading instructions")
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name("summariser_v1.md")
    return f"{prompt}\n\n{ctx.deps.document_markdown}"
