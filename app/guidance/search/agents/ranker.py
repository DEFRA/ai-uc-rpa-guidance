"""Ranker agent: finds the index entries that answer an operator's query."""

import logging

import pydantic_ai

from app.guidance.search import models

logger = logging.getLogger(__name__)

MAX_RESULTS = 10

ranker_agent = pydantic_ai.Agent(
    deps_type=models.RankDependencies,
    output_type=models.RankedResults,
    retries={"output": 2},
)


@ranker_agent.output_validator
async def validate_results_exist(
    ctx: pydantic_ai.RunContext[models.RankDependencies],
    output: models.RankedResults,
) -> models.RankedResults:
    """Drop any result the index does not hold, and cap the list.

    A result is rendered as a link, so one naming a document or section that
    does not exist would take the operator nowhere. Returning nothing is a
    valid answer — a query with no match must say so rather than reach.
    """
    grounded = [
        result
        for result in output.results
        if (result.document_id, result.section_number) in ctx.deps.entries
    ]

    dropped = len(output.results) - len(grounded)
    if dropped:
        logger.info("[Search] Dropped %d result(s) absent from the index", dropped)

    return output.model_copy(update={"results": grounded[:MAX_RESULTS]})


@ranker_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.RankDependencies],
) -> str:
    """Compose instructions: the ranker prompt, then the whole index."""
    logger.info("[Search] Ranker: index is %d characters", len(ctx.deps.index))
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name("ranker_v1.md")

    return f"{prompt}\n\n# The index\n\n{ctx.deps.index}"
