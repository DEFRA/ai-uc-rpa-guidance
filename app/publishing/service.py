"""Business logic service for document analysis."""

import logging

from app.infra.bedrock import llm
from app.publishing import aggregation, api_schemas, models, ordering
from app.publishing.agents import aggregator, checker

logger = logging.getLogger(__name__)


async def analyse_document(
    document_title: str,
    sections: list[models.DocumentSection],
) -> api_schemas.AnalyseResponse:
    """Analyse a guidance document for quality issues, one section at a time.

    Runs the checker agent sequentially over each top-level section's subtree,
    merges findings and good points mechanically, computes the overall verdict
    as worst-of the section verdicts, and asks the aggregator agent to
    synthesize the per-section summaries into one document-level summary.

    Args:
        document_title: The document's title, from its stored metadata.
        sections: Each top-level section's full subtree Markdown.

    Returns:
        Structured analysis response with findings and summary.

    Raises:
        TimeoutError: If an analysis request times out.
        Exception: Other exceptions from the LLM or agent execution bubble up.
    """
    logger.info(
        "[Publishing] Starting document analysis of %d section(s)", len(sections)
    )

    if not sections:
        logger.info("[Publishing] No sections to analyse")
        return api_schemas.AnalyseResponse(
            status="completed",
            document_title=document_title,
            findings=[],
            good_points=[],
            summary="No content was found to analyse.",
            verdict=models.ReadinessVerdict.READY.value,
            usage=api_schemas.TokenUsage(input_tokens=0, output_tokens=0),
        )

    section_outputs: list[models.AnalysisOutput] = []
    input_tokens = 0
    output_tokens = 0

    for section in sections:
        logger.info("[Publishing] Analysing section %s", section.number)
        result = await checker.checker_agent.run(
            "Analyse the provided guidance document section for quality issues.",
            deps=models.AgentDependencies(document_text=section.text),
            model=llm.claude_sonnet,
        )
        section_outputs.append(result.output)
        if result.usage:
            input_tokens += result.usage.input_tokens
            output_tokens += result.usage.output_tokens

    findings = ordering.order_findings(
        [finding for output in section_outputs for finding in output.findings]
    )
    good_points = aggregation.merge_good_points(
        [output.good_points for output in section_outputs]
    )
    verdict = aggregation.compute_verdict(
        [output.verdict for output in section_outputs]
    )

    agg_result = await aggregator.aggregator_agent.run(
        "Synthesize a single document-level summary from the per-section summaries.",
        deps=models.AggregatorDependencies(
            section_summaries=[
                models.SectionSummary(
                    section_number=section.number,
                    summary=output.summary,
                    verdict=output.verdict,
                )
                for section, output in zip(sections, section_outputs, strict=True)
            ],
            overall_verdict=verdict,
        ),
        model=llm.claude_sonnet,
    )
    if agg_result.usage:
        input_tokens += agg_result.usage.input_tokens
        output_tokens += agg_result.usage.output_tokens

    logger.info("[Publishing] Analysis completed successfully")

    return api_schemas.AnalyseResponse(
        status="completed",
        document_title=document_title,
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
        good_points=good_points,
        summary=agg_result.output.summary,
        verdict=verdict.value,
        usage=api_schemas.TokenUsage(
            input_tokens=input_tokens, output_tokens=output_tokens
        ),
    )
