"""Reviewer agent: reviews guidance against the design principles."""

import html
import logging
import re
import unicodedata

import pydantic_ai

from app.review import models

logger = logging.getLogger(__name__)

reviewer_agent = pydantic_ai.Agent(
    deps_type=models.AgentDependencies,
    output_type=models.ReviewOutput,
    retries={"output": 2},
)


_TAG_RE = re.compile(r"<[^>]+>")

# Map common "smart" typography to ASCII so quotes still anchor when the model
# reproduces punctuation differently from the parsed document.
_TYPOGRAPHY = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "–": "-",
        "—": "-",
        "‒": "-",
        "−": "-",
        "…": "...",
    }
)


def _normalise(text: str) -> str:
    """Normalise text for verbatim quote matching.

    The document under review is HTML-laden markdown (inline <a>/<strong> tags,
    escaped entities) and uses smart punctuation, while the model quotes clean
    prose. Strip tags, unescape entities, and fold typography, case and
    whitespace so a genuine quote anchors despite these differences.
    """
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_TYPOGRAPHY)
    return " ".join(text.split()).casefold()


def _split_by_anchoring[ItemT: (models.ReviewFinding, models.GoodPoint)](
    items: list[ItemT], document: str
) -> tuple[list[ItemT], list[ItemT]]:
    """Bucket quote-bearing items into (anchored, unanchored)."""
    anchored: list[ItemT] = []
    unanchored: list[ItemT] = []
    for item in items:
        bucket = anchored if _normalise(item.quote) in document else unanchored
        bucket.append(item)
    return anchored, unanchored


@reviewer_agent.output_validator
async def validate_quotes_are_verbatim(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
    output: models.ReviewOutput,
) -> models.ReviewOutput:
    """Anchor every finding and good-point example to real document text.

    On earlier attempts the model is asked to retry with corrected quotes. On
    the final attempt any still-unanchored items are dropped rather than
    failing the whole review, so a single stray quote can't sink the job.
    """
    document = _normalise(ctx.deps.document_text)
    findings, bad_findings = _split_by_anchoring(output.findings, document)
    good_points, bad_good_points = _split_by_anchoring(output.good_points, document)

    if not bad_findings and not bad_good_points:
        return output

    if ctx.last_attempt:
        logger.warning(
            "[Review] Dropping %d unanchored finding(s) and %d good point(s) "
            "on final attempt",
            len(bad_findings),
            len(bad_good_points),
        )
        return output.model_copy(
            update={"findings": findings, "good_points": good_points}
        )

    unanchored: list[tuple[models.ReviewFinding | models.GoodPoint, str]] = [
        *((finding, finding.section) for finding in bad_findings),
        *((point, "good_points") for point in bad_good_points),
    ]
    details = "\n".join(
        f"- {item.principle} @ {where}: {item.quote[:120]!r}"
        for item, where in unanchored
    )
    logger.info(
        "[Review] Reviewer output rejected: %d unanchored quotes",
        len(bad_findings) + len(bad_good_points),
    )
    msg = (
        "Each finding's and good point's `quote` must be copied verbatim from "
        "the document under review. The following quotes were not found in "
        "the document:\n"
        f"{details}\n"
        "Correct each quote to an exact excerpt, or drop any item you cannot "
        "anchor to the document text."
    )
    raise pydantic_ai.ModelRetry(msg)


@reviewer_agent.instructions
async def get_instructions(
    ctx: pydantic_ai.RunContext[models.AgentDependencies],
) -> str:
    """Compose instructions: reviewer prompt followed by the document."""
    logger.info("[Review] Reviewer agent: loading instructions")
    prompt = await ctx.deps.prompt_repository.get_prompt_by_name("reviewer_v1.md")
    return f"{prompt}\n\n{ctx.deps.document_text}"
