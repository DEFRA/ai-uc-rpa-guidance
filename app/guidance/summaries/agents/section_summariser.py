"""Section summariser agent: one index entry per section of a document."""

import logging

import pydantic_ai

from app.guidance.summaries import models

logger = logging.getLogger(__name__)

section_summariser_agent = pydantic_ai.Agent(
    deps_type=models.SectionSummaryDependencies,
    output_type=models.SectionSummariesOutput,
    retries={"output": 2},
)


@section_summariser_agent.output_validator
async def validate_sections_are_the_documents_own(
    ctx: pydantic_ai.RunContext[models.SectionSummaryDependencies],
    output: models.SectionSummariesOutput,
) -> models.SectionSummariesOutput:
    """Keep every entry anchored to a section the parse actually found.

    An entry against a number the document does not have would be a link to
    nowhere, so unknown numbers are dropped. A section left unsummarised is
    a hole in the index, so on earlier attempts the model is asked for the
    ones it missed; on the final attempt what was produced stands.
    """
    known = {number for number, _ in ctx.deps.sections}

    entries = [entry for entry in output.sections if entry.number in known]
    invented = [entry.number for entry in output.sections if entry.number not in known]
    missing = sorted(known - {entry.number for entry in entries})

    if invented:
        logger.info("[Summary] Dropping %d invented section(s)", len(invented))

    if not missing or ctx.last_attempt:
        if missing:
            logger.warning(
                "[Summary] %d section(s) left unsummarised on final attempt",
                len(missing),
            )
        return output.model_copy(update={"sections": entries})

    msg = (
        "Every section of the document needs an entry, and each entry's "
        "`number` must be one of the section numbers given. "
        f"These sections have no entry: {', '.join(missing)}. "
        "Return an entry for each of them, alongside the ones already written."
    )
    raise pydantic_ai.ModelRetry(msg)


@section_summariser_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.SectionSummaryDependencies],
) -> str:
    """Compose instructions: prompt, the sections to cover, then the document."""
    logger.info("[Summary] Section summariser agent: loading instructions")
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name(
        "section_summariser_v1.md"
    )
    sections = "\n".join(
        f"- {number}: {heading}" for number, heading in ctx.deps.sections
    )

    return (
        f"{prompt}\n\n"
        f"# Document\n\n{ctx.deps.document_title}\n\n"
        f"# Sections to summarise\n\n{sections}\n\n"
        f"# The document\n\n{ctx.deps.document_markdown}"
    )
