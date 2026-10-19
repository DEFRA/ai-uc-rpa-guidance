"""Check a prototype guide against the quality rules, top-level section by section."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

import pydantic_ai.agent

from app.infra.bedrock import llm
from app.quality import anchors, api_schemas, models
from app.quality.agents import checker

logger = logging.getLogger(__name__)

# How many sections are checked at the same time. Two at once was tried on
# 2026-10-19: a 5-section guide took 439 s against about 220 s one at a time,
# with the same tokens, so Bedrock appears to serve parallel calls no faster.
CONCURRENT_CHECKS = 1


async def check_guide(
    document_title: str,
    parts: list[anchors.Part],
    on_part_done: Callable[[], Awaitable[None]] | None = None,
) -> api_schemas.QualityCheckResult:
    """Run the checker over the parts, a few at once, and turn its findings into comments.

    Unlike the publishing check there is no aggregator: the comments are the
    result. At most CONCURRENT_CHECKS parts are checked at the same time.

    Args:
        document_title: The guide's title.
        parts: The guide's top-level sections, each with its subsections.
        on_part_done: Called as each part's check finishes, to report progress.

    Returns:
        Every comment, in document order, and the tokens used.
    """
    logger.info("[Quality] Checking %d section(s)", len(parts))

    slots = asyncio.Semaphore(CONCURRENT_CHECKS)
    results = await asyncio.gather(
        *(_check_part(part, slots, on_part_done) for part in parts)
    )

    comments: list[api_schemas.QualityComment] = []
    input_tokens = 0
    output_tokens = 0

    for part, result in zip(parts, results, strict=True):
        comments.extend(_comments(part, result.output.findings))
        if result.usage:
            input_tokens += result.usage.input_tokens
            output_tokens += result.usage.output_tokens

    return api_schemas.QualityCheckResult(
        document_title=document_title,
        comments=comments,
        usage=api_schemas.TokenUsage(
            input_tokens=input_tokens, output_tokens=output_tokens
        ),
    )


async def _check_part(
    part: anchors.Part,
    slots: asyncio.Semaphore,
    on_part_done: Callable[[], Awaitable[None]] | None,
) -> pydantic_ai.agent.AgentRunResult[models.CheckerOutput]:
    """Check one part once a slot is free, then report it done."""
    async with slots:
        logger.info("[Quality] Checking section %s", part.label)
        result = await checker.checker_agent.run(
            "Check the provided guidance section against the quality rules.",
            deps=models.AgentDependencies(part=part),
            model=llm.claude_sonnet,
        )
    if on_part_done is not None:
        await on_part_done()
    return result


def _comments(
    part: anchors.Part, findings: list[models.QualityFinding]
) -> list[api_schemas.QualityComment]:
    """A part's findings as comments, in the order their quotes appear."""
    anchored = [
        (anchor, finding)
        for finding in findings
        if (anchor := anchors.anchor(part, finding.exact)) is not None
    ]
    anchored.sort(key=lambda pair: part.markdown.index(pair[0].exact))

    return [_comment(anchor, finding) for anchor, finding in anchored]


def _comment(
    anchor: anchors.Anchor, finding: models.QualityFinding
) -> api_schemas.QualityComment:
    """One finding as a comment."""
    return api_schemas.QualityComment(
        comment_id=uuid.uuid4(),
        rule_id=finding.rule_id.value,
        rule_version=models.RULE_VERSIONS[finding.rule_id],
        section=anchor.section,
        anchor=api_schemas.Anchor(
            prefix=anchor.prefix, exact=anchor.exact, suffix=anchor.suffix
        ),
        issue=finding.issue,
        why_it_matters=finding.why_it_matters,
        recommendation=finding.recommendation,
        confidence=finding.confidence.value,
    )
