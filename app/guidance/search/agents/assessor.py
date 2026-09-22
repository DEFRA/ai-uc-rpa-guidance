"""Assessor agent: reads a section in full and judges it against the query."""

import logging

import pydantic_ai

from app.guidance.search import models

logger = logging.getLogger(__name__)

assessor_agent = pydantic_ai.Agent(
    deps_type=models.AssessDependencies,
    output_type=models.Relevance,
)


@assessor_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.AssessDependencies],
) -> str:
    """Compose instructions: the assessor prompt, then the section itself."""
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name("assessor_v1.md")

    return (
        f"{prompt}\n\n"
        f"# The query\n\n{ctx.deps.query}\n\n"
        f"# The section\n\n"
        f"From *{ctx.deps.document_title}*, section '{ctx.deps.heading}'.\n\n"
        f"{ctx.deps.section_markdown}"
    )
