"""Answerer agent: writes the overview shown above the results."""

import logging

import pydantic_ai

from app.guidance.search import models

logger = logging.getLogger(__name__)

answerer_agent = pydantic_ai.Agent(
    deps_type=models.AnswerDependencies,
    output_type=models.SearchAnswer,
    retries={"output": 2},
)


@answerer_agent.output_validator
async def validate_citations_are_results(
    ctx: pydantic_ai.RunContext[models.AnswerDependencies],
    output: models.SearchAnswer,
) -> models.SearchAnswer:
    """Keep the answer's citations to results the operator can actually see.

    A citation is rendered as a link beside the answer, so one that is not in
    the result list would send the operator somewhere the page does not show.
    """
    cited = [
        citation
        for citation in output.cited
        if (citation.document_id, citation.section_number) in ctx.deps.cited
    ]

    dropped = len(output.cited) - len(cited)
    if dropped:
        logger.info("[Search] Dropped %d citation(s) outside the results", dropped)

    return output.model_copy(update={"cited": cited})


@answerer_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.AnswerDependencies],
) -> str:
    """Compose instructions: the answerer prompt, the query, then the results."""
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name("answerer_v1.md")

    return (
        f"{prompt}\n\n"
        f"# The query\n\n{ctx.deps.query}\n\n"
        f"# The results\n\n{ctx.deps.results}"
    )
