"""Quality checker agent: checks one top-level section of a prototype guide."""

import logging

import pydantic_ai

from app.quality import anchors, models

logger = logging.getLogger(__name__)

PROMPT_NAME = "checker.md"

checker_agent = pydantic_ai.Agent(
    deps_type=models.AgentDependencies,
    output_type=models.CheckerOutput,
    retries={"output": 2},
)


@checker_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
) -> str:
    """Load the rules and append the section to check."""
    logger.info("[Quality Agent] Loading instructions")
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name(PROMPT_NAME)
    return f"{prompt}\n\n{ctx.deps.part.markdown}"


@checker_agent.output_validator
async def validate_quotes_anchor(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
    output: models.CheckerOutput,
) -> models.CheckerOutput:
    """Make every finding's quote one, and only one, place in the section.

    On earlier attempts the model is asked to correct the quotes that do not
    anchor. On the final attempt those findings are dropped rather than
    failing the whole check, so one stray quote cannot sink the job.
    """
    not_found: list[str] = []
    repeated: list[str] = []
    anchored: list[models.QualityFinding] = []
    for finding in output.findings:
        if anchors.anchor(ctx.deps.part, finding.exact) is not None:
            anchored.append(finding)
        elif finding.exact in ctx.deps.part.markdown:
            repeated.append(finding.exact)
        else:
            not_found.append(finding.exact)

    if not not_found and not repeated:
        return output

    if ctx.last_attempt:
        logger.warning(
            "[Quality Agent] Dropping %d unanchored finding(s) on final attempt",
            len(not_found) + len(repeated),
        )
        return output.model_copy(update={"findings": anchored})

    raise pydantic_ai.ModelRetry(_retry_message(not_found, repeated))


def _retry_message(not_found: list[str], repeated: list[str]) -> str:
    """Tell the model which quotes failed to anchor, and why."""
    lines = ["Each finding's `exact` must be copied from the section and be unique."]
    if not_found:
        lines.append("These were not found in the section:")
        lines.extend(f"- {quote[:120]!r}" for quote in not_found)
    if repeated:
        lines.append("These appear more than once in the section:")
        lines.extend(f"- {quote[:120]!r}" for quote in repeated)
    lines.append(
        "Copy each quote character for character, widen any repeated quote "
        "until it is unique, or drop a finding you cannot anchor."
    )
    return "\n".join(lines)
