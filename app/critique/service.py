"""Critic -> writer -> critic orchestration for guidance language review."""

import dataclasses
import logging

import pydantic_ai
import pydantic_ai.usage

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


@dataclasses.dataclass
class _LoopState:
    """Mutable state threaded through the critic/writer loop."""

    document: str
    first_critique: models.CritiqueOutput | None = None
    previous_findings: list[models.CritiqueFinding] = dataclasses.field(
        default_factory=list
    )
    history: list[api_schemas.CritiqueIterationSummary] = dataclasses.field(
        default_factory=list
    )
    approved: bool = False
    input_tokens: int = 0
    output_tokens: int = 0

    def add_usage(self, usage: pydantic_ai.usage.RunUsage | None) -> None:
        if usage:
            self.input_tokens += usage.input_tokens or 0
            self.output_tokens += usage.output_tokens or 0


async def _run_critic_pass(
    state: _LoopState,
    iteration: int,
    usage_limits: pydantic_ai.UsageLimits,
) -> models.CritiqueOutput:
    """Review the current document and record the iteration in the history."""
    result = await critic.critic_agent.run(
        _CRITIC_USER_PROMPT,
        deps=models.AgentDependencies(
            document_text=state.document,
            previous_findings=state.previous_findings,
        ),
        model=llm.claude_sonnet,
        usage_limits=usage_limits,
    )
    state.add_usage(result.usage)

    critique = result.output
    if state.first_critique is None:
        state.first_critique = critique

    state.history.append(
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
    return critique


async def _run_writer_pass(
    state: _LoopState,
    findings: list[models.CritiqueFinding],
    usage_limits: pydantic_ai.UsageLimits,
    original_document: str,
) -> None:
    """Apply the findings to the current document and log any invariant drift."""
    result = await writer.writer_agent.run(
        _WRITER_USER_PROMPT,
        deps=models.AgentDependencies(
            document_text=state.document,
            findings_to_apply=findings,
        ),
        model=llm.claude_sonnet,
        usage_limits=usage_limits,
    )
    state.add_usage(result.usage)
    state.document = result.output.revised_document

    for warning in invariants.check_invariants(original_document, state.document):
        logger.warning("[Critique] Invariant violation: %s", warning)


def _resolve_status(approved: bool, revise: bool) -> str:
    if approved:
        return "approved"
    if revise:
        return "max_iterations_reached"
    return "review_completed"


def _build_response(
    state: _LoopState, original_document: str, revise: bool
) -> api_schemas.CritiqueResponse:
    was_revised = state.document != original_document
    invariant_warnings = (
        invariants.check_invariants(original_document, state.document)
        if was_revised
        else []
    )

    logger.info(
        "[Critique] Run complete: approved=%s iterations=%d warnings=%d",
        state.approved,
        len(state.history),
        len(invariant_warnings),
    )

    return api_schemas.CritiqueResponse(
        status=_resolve_status(state.approved, revise),
        iterations=len(state.history),
        revised_document=state.document if was_revised else None,
        reports=_build_reports(state.first_critique) if state.first_critique else [],
        critique_history=state.history,
        invariant_warnings=invariant_warnings,
        usage=api_schemas.TokenUsage(
            input_tokens=state.input_tokens,
            output_tokens=state.output_tokens,
        ),
    )


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

    state = _LoopState(document=document_text)

    for iteration in range(1, iteration_cap + 1):
        critique = await _run_critic_pass(state, iteration, usage_limits)
        if critique.approved:
            state.approved = True
            break

        state.previous_findings = critique.findings
        if iteration == iteration_cap:
            break

        await _run_writer_pass(state, critique.findings, usage_limits, document_text)

    return _build_response(state, document_text, revise)
