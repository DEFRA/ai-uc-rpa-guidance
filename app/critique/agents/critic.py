"""Critic agent: reviews guidance documents against GDS and DEFRA standards."""

import asyncio
import json
import logging

import pydantic_ai

from app.critique import models, tools
from app.infra.context import repository as context_repo

logger = logging.getLogger(__name__)

critic_agent = pydantic_ai.Agent(
    deps_type=models.AgentDependencies,
    output_type=models.CritiqueOutput,
    toolsets=[tools.context_documents_toolset],
    retries={"output": 2},
)


def _normalise(text: str) -> str:
    """Collapse whitespace so quote matching tolerates wrapping differences."""
    return " ".join(text.split())


@critic_agent.output_validator
async def validate_quotes_are_verbatim(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
    output: models.CritiqueOutput,
) -> models.CritiqueOutput:
    """Reject findings whose quote cannot be found in the document.

    Anchors every finding to real document text; the model is asked to retry
    with corrected quotes (or to drop findings it cannot anchor).
    """
    document = _normalise(ctx.deps.document_text)
    unanchored = [
        finding
        for finding in output.findings
        if _normalise(finding.quote) not in document
    ]

    if unanchored:
        details = "\n".join(
            f"- {finding.rule_reference} @ {finding.where}: {finding.quote[:120]!r}"
            for finding in unanchored
        )
        logger.info(
            "[Critique] Critic output rejected: %d unanchored quotes", len(unanchored)
        )
        msg = (
            "Each finding's `quote` must be copied verbatim from the document "
            "under review. The following quotes were not found in the document:\n"
            f"{details}\n"
            "Correct each quote to an exact excerpt, or drop any finding you "
            "cannot anchor to the document text."
        )
        raise pydantic_ai.ModelRetry(msg)

    return output


# The reference-document catalogues are inlined into the instructions so the
# critic can pick every rule it needs and fetch them in one batched turn,
# instead of spending tool round-trips listing the indexes.
_INDEXES = (
    ("GDS style guide rules (standard: gds)", tools.STYLE_GUIDE_INDEX),
    ("GDS writing guidance (standard: gds)", tools.CONTENT_GUIDANCE_INDEX),
    (
        "DEFRA style guide sections (standard: defra_style)",
        tools.DEFRA_STYLE_GUIDE_INDEX,
    ),
)


def _format_index(raw: str) -> str:
    """Render an index.json as compact 'title — file' lines for the prompt."""
    try:
        entries = json.loads(raw)
        return "\n".join(
            f"- {entry.get('title', '?')} — file: {entry.get('file', '?')}"
            for entry in entries
        )
    except json.JSONDecodeError, AttributeError, TypeError:
        return raw


async def _load_index(
    repository: context_repo.AbstractContextRepository, key: str
) -> str:
    try:
        return _format_index(await repository.get_context(key))
    except context_repo.ContextRepositoryError as e:
        logger.warning("[Critique] Could not inline context index '%s': %s", key, e)
        return "(catalogue unavailable — use the list tools to discover documents)"


@critic_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
) -> str:
    """Compose instructions: critic prompt, reference catalogues, document."""
    logger.info("[Critique] Critic agent: loading instructions")
    prompt, *index_blocks = await asyncio.gather(
        ctx.deps.prompt_repository.get_prompt_by_name("critic.md"),
        *[_load_index(ctx.deps.context_repository, key) for _, key in _INDEXES],
    )

    parts = [prompt, "# Reference document catalogues"]
    for (title, _), block in zip(_INDEXES, index_blocks, strict=True):
        parts.append(f"## {title}\n\n{block}")

    parts.extend(["# Document under review", ctx.deps.document_text])

    if ctx.deps.previous_findings:
        findings_json = "[\n"
        findings_json += ",\n".join(
            f.model_dump_json(indent=2) for f in ctx.deps.previous_findings
        )
        findings_json += "\n]"
        parts.extend(
            [
                "# Previous review findings",
                "The document above is a revision. Verify each of these findings "
                "has been resolved and that the revision is text-level only:",
                findings_json,
            ]
        )

    return "\n\n".join(parts)
