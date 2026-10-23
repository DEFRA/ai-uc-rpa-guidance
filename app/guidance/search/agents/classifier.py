"""Classifier agent: decides whether a search is natural language or keywords."""

import logging

import pydantic_ai

from app.guidance.search import models

logger = logging.getLogger(__name__)

classifier_agent = pydantic_ai.Agent(
    deps_type=models.ClassifyDependencies,
    output_type=models.QueryKind,
)


@classifier_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.ClassifyDependencies],
) -> str:
    """The classifier prompt; the search itself is the user message."""
    return await ctx.deps.prompt_repository.get_prompt_by_name("classifier_v1.md")
