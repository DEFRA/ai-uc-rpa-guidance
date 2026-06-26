"""Business logic service for document analysis."""

import logging

from app.infra.bedrock import llm
from app.publishing import api_schemas, models, ordering
from app.publishing.agents import checker

logger = logging.getLogger(__name__)


async def analyse_document(
    document_text: str,
) -> api_schemas.AnalyseResponse:
    """Analyse a guidance document for quality issues.

    Accepts document text and returns structured analysis findings including
    identified issues, severity levels, and recommendations for remediation.

    Args:
        document_text: The text of the guidance document to analyze.
        prompt_repository: The prompt repository for loading prompts.

    Returns:
        Structured analysis response with findings and summary.

    Raises:
        TimeoutError: If the analysis request times out.
        Exception: Other exceptions from the LLM or agent execution bubble up.
    """
    logger.info("[Publishing] Starting document analysis")

    deps = models.AgentDependencies(
        document_text=document_text,
    )

    result = await checker.checker_agent.run(
        "Analyse the provided guidance document for quality issues.",
        deps=deps,
        model=llm.claude_sonnet,
    )

    logger.info("[Publishing] Analysis completed successfully")

    findings = ordering.order_findings(result.output.findings)

    return api_schemas.AnalyseResponse(
        status="completed",
        document_title=result.output.document_title,
        findings=[
            api_schemas.FindingResponse(
                category=f.category.value,
                section=f.section,
                issue=f.issue,
                why_it_matters=f.why_it_matters,
                severity=f.severity.value,
                confidence=f.confidence.value,
                recommendation=f.recommendation,
            )
            for f in findings
        ],
        good_points=result.output.good_points,
        summary=result.output.summary,
        verdict=result.output.verdict.value,
        usage=(
            api_schemas.TokenUsage(
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )
            if result.usage
            else None
        ),
    )
