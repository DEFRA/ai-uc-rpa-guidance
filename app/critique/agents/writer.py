"""Writer agent: applies critic findings as text-level revisions."""

import logging

import pydantic_ai

from app.critique import invariants, models

logger = logging.getLogger(__name__)

# No context toolset: findings carry the precise fix, and giving the writer
# tool access caused it to crawl the style-guide indexes until it hit
# pydantic-ai's request limit.
writer_agent = pydantic_ai.Agent(
    deps_type=models.AgentDependencies,
    output_type=models.RevisionOutput,
    retries={"output": 2},
)


@writer_agent.output_validator
async def validate_structure_preserved(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
    output: models.RevisionOutput,
) -> models.RevisionOutput:
    """Reject revisions that break the AC5 preservation invariants.

    Compares the revision against the document the writer was given: image
    references and link URLs must survive verbatim, and the heading structure
    (count and levels) must be unchanged.
    """
    warnings = invariants.check_invariants(
        ctx.deps.document_text, output.revised_document
    )

    if warnings:
        details = "\n".join(f"- {warning}" for warning in warnings)
        logger.info(
            "[Critique] Writer output rejected: %d invariant violations",
            len(warnings),
        )
        msg = (
            "The revision broke the preservation rules. Text-level changes "
            "only — every image reference and hyperlink must be carried "
            "through unchanged, and headings may be reworded but never added, "
            f"removed, or change level. Violations:\n{details}\n"
            "Produce the complete revision again with these corrected."
        )
        raise pydantic_ai.ModelRetry(msg)

    return output


@writer_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
) -> str:
    """Compose system instructions: writer prompt, document, findings to apply."""
    logger.info("[Critique] Writer agent: loading instructions")
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name("writer.md")

    findings_json = "[\n"
    findings_json += ",\n".join(
        f.model_dump_json(indent=2) for f in ctx.deps.findings_to_apply
    )
    findings_json += "\n]"

    return "\n\n".join(
        [
            prompt,
            "# Document to revise",
            ctx.deps.document_text,
            "# Findings to apply",
            findings_json,
        ]
    )
