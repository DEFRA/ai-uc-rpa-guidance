"""Aggregator agent: synthesizes one document-level summary from section summaries."""

import logging

import pydantic_ai

from app.publishing import models

logger = logging.getLogger(__name__)

aggregator_agent = pydantic_ai.Agent(
    deps_type=models.AggregatorDependencies,
    output_type=models.AggregatedSummary,
)


@aggregator_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.AggregatorDependencies],
) -> str:
    """Retrieve system instructions plus the serialized per-section summaries."""
    logger.info("[Publishing Aggregator] Loading instructions")
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name("aggregator.md")
    sections_text = "\n\n".join(
        f"Section {s.section_number} (verdict: {s.verdict.value}):\n{s.summary}"
        for s in ctx.deps.section_summaries
    )
    return (
        f"{prompt}\n\n"
        f"OVERALL VERDICT (already decided, do not contradict it): "
        f"{ctx.deps.overall_verdict.value}\n\n"
        f"PER-SECTION SUMMARIES:\n{sections_text}"
    )
