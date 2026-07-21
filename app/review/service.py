"""Business logic service for guidance review."""

import logging

from app.infra.bedrock import llm
from app.review import api_schemas, models, ordering
from app.review.agents import reviewer

logger = logging.getLogger(__name__)


def _map_principle_ratings(
    ratings: models.PrincipleRatings,
) -> api_schemas.PrincipleRatingsResponse:
    """Map the domain principle ratings to the flat API shape.

    The domain rating carries a justification the LLM writes before
    committing to each rating; only the rating itself is exposed — the
    evidence users see lives in the findings.
    """
    responses: dict[str, str] = {}
    for name in models.PrincipleRatings.model_fields:
        rating: models.PrincipleRating = getattr(ratings, name)
        responses[name] = rating.rating.value
    return api_schemas.PrincipleRatingsResponse(**responses)


async def review_document(
    document_text: str,
) -> api_schemas.ReviewResponse:
    """Review a guidance document against the design principles.

    Runs the reviewer agent over the document and maps the result to the
    API response shape.

    Args:
        document_text: The text of the guidance document to review.

    Returns:
        Structured review response with ratings, feedback, and the usability
        verdict.

    Raises:
        TimeoutError: If the review request times out.
        Exception: Other exceptions from the LLM or agent execution bubble up.
    """
    logger.info("[Review] Starting document review")

    deps = models.AgentDependencies(
        document_text=document_text,
    )

    result = await reviewer.reviewer_agent.run(
        "Review the provided guidance document against the design principles.",
        deps=deps,
        model=llm.claude_sonnet,
    )

    logger.info("[Review] Review completed successfully")

    output = result.output
    findings = ordering.order_findings(output.findings)

    return api_schemas.ReviewResponse(
        status="completed",
        document_title=output.document_title,
        task_context=api_schemas.TaskContextResponse(
            task=output.task_context.task,
            user=output.task_context.user,
            usage_context=output.task_context.usage_context,
        ),
        usability=api_schemas.UsabilityResponse(
            verdict=output.usability.verdict.value,
            explanation=output.usability.explanation,
        ),
        principle_ratings=_map_principle_ratings(output.principle_ratings),
        good_points=[
            api_schemas.GoodPointResponse(
                principle=p.principle.value,
                quote=p.quote,
                comment=p.comment,
            )
            for p in output.good_points
        ],
        findings=[
            api_schemas.FindingResponse(
                principle=f.principle.value,
                section=f.section,
                quote=f.quote,
                issue=f.issue,
                why_it_matters=f.why_it_matters,
                severity=f.severity.value,
                confidence=f.confidence.value,
                recommendation=f.recommendation,
            )
            for f in findings
        ],
        usage=(
            api_schemas.TokenUsage(
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )
            if result.usage
            else None
        ),
    )
