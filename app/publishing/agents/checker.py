"""Publishing QA agent for analyzing guidance documents."""

import logging

import pydantic_ai

from app.publishing import models

logger = logging.getLogger(__name__)

checker_agent = pydantic_ai.Agent(
    deps_type=models.AgentDependencies,
    output_type=models.AnalysisOutput,
)


@checker_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
) -> str:
    """Retrieve system instructions for the publishing QA agent."""
    logger.info("[Publishing Agent] Loading instructions")
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name("checker.md")
    return f"{prompt}\n\n{ctx.deps.document_text}"
