"""Critic -> writer -> critic orchestration for guidance language review."""

import logging

import pydantic_ai

from app import config
from app.critique import api_schemas, invariants, models
from app.critique.agents import critic, writer
from app.infra.bedrock import llm

logger = logging.getLogger(__name__)

_CRITIC_USER_PROMPT = (
    "Review the provided guidance document against the GDS content guidelines "
    "and the DEFRA style guide."
)
_WRITER_USER_PROMPT = (
    "Revise the provided guidance document, applying every finding through "
    "text-level changes only."
)


def _resolve_iteration_cap(requested: int | None) -> int:
    configured = config.get_config().critique_max_iterations
    if requested is None:
        return configured
    return min(requested, configured)


def _build_reports(critique: models.CritiqueOutput) -> list[api_schemas.StandardReport]:
    """Group the first critique pass into one report per standard (AC2/AC3)."""
    conformance_by_standard = {c.standard: c.summary for c in critique.conformance}

    return [
        api_schemas.StandardReport(
            standard=standard.value,
            conformance_summary=conformance_by_standard.get(standard, ""),
            findings=[
                api_schemas.FindingResponse(
                    rule_reference=f.rule_reference,
                    what=f.what,
                    where=f.where,
                    quote=f.quote,
                    why=f.why,
                    fix=f.fix,
                    severity=f.severity.value,
                )
                for f in critique.findings
                if f.standard == standard
            ],
        )
        for standard in models.Standard
    ]


async def critique_document(
    document_text: str,
    max_iterations: int | None = None,
    revise: bool = False,
) -> api_schemas.CritiqueResponse:
    """Review a guidance document, optionally revising it in a critic/writer loop.

    With revise disabled (the default for the POC — the writer pass is by far
    the slowest stage), a single critique pass produces the reports. With
    revise enabled, the writer applies the findings and the critic re-reviews
    the revision, up to the iteration cap.

    Args:
        document_text: The markdown document to review.
        max_iterations: Optional requested cap; bounded by server config.
            Only used when revise is true.
        revise: Whether to run the writer/re-review loop.

    Returns:
        Structured response with per-standard reports (from the review of the
        original document), the revised document (null unless a revision was
        produced), loop history, invariant warnings, and accumulated usage.
    """
    iteration_cap = _resolve_iteration_cap(max_iterations) if revise else 1
    # Per-agent-run request cap, matching the swarm runtime's explicit
    # UsageLimits convention rather than relying on the library default.
    usage_limits = pydantic_ai.UsageLimits(
        request_limit=config.get_config().critique_request_limit
    )
    logger.info(
        "[Critique] Starting critique run (revise=%s, cap=%d)", revise, iteration_cap
    )

    current_document = document_text
    previous_findings: list[models.CritiqueFinding] = []
    history: list[api_schemas.CritiqueIterationSummary] = []
    first_critique: models.CritiqueOutput | None = None
    approved = False
    input_tokens = 0
    output_tokens = 0

    for iteration in range(1, iteration_cap + 1):
        critic_deps = models.AgentDependencies(
            document_text=current_document,
            previous_findings=previous_findings,
        )
        critic_result = await critic.critic_agent.run(
            _CRITIC_USER_PROMPT,
            deps=critic_deps,
            model=llm.claude_sonnet,
            usage_limits=usage_limits,
        )
        if critic_result.usage:
            input_tokens += critic_result.usage.input_tokens or 0
            output_tokens += critic_result.usage.output_tokens or 0

        critique = critic_result.output
        if first_critique is None:
            first_critique = critique

        history.append(
            api_schemas.CritiqueIterationSummary(
                iteration=iteration,
                approved=critique.approved,
                summary=critique.summary,
                finding_count=len(critique.findings),
            )
        )
        logger.info(
            "[Critique] Iteration %d: approved=%s findings=%d",
            iteration,
            critique.approved,
            len(critique.findings),
        )

        if critique.approved:
            approved = True
            break

        previous_findings = critique.findings

        if iteration == iteration_cap:
            break

        writer_deps = models.AgentDependencies(
            document_text=current_document,
            findings_to_apply=critique.findings,
        )
        writer_result = await writer.writer_agent.run(
            _WRITER_USER_PROMPT,
            deps=writer_deps,
            model=llm.claude_sonnet,
            usage_limits=usage_limits,
        )
        if writer_result.usage:
            input_tokens += writer_result.usage.input_tokens or 0
            output_tokens += writer_result.usage.output_tokens or 0

        current_document = writer_result.output.revised_document

        for warning in invariants.check_invariants(document_text, current_document):
            logger.warning("[Critique] Invariant violation: %s", warning)

    was_revised = current_document != document_text
    invariant_warnings = (
        invariants.check_invariants(document_text, current_document)
        if was_revised
        else []
    )

    logger.info(
        "[Critique] Run complete: approved=%s iterations=%d warnings=%d",
        approved,
        len(history),
        len(invariant_warnings),
    )

    if approved:
        status = "approved"
    elif revise:
        status = "max_iterations_reached"
    else:
        status = "review_completed"

    return api_schemas.CritiqueResponse(
        status=status,
        iterations=len(history),
        revised_document=current_document if was_revised else None,
        reports=_build_reports(first_critique) if first_critique else [],
        critique_history=history,
        invariant_warnings=invariant_warnings,
        usage=api_schemas.TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        ),
    )
