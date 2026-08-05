"""Tests for the per-section analysis orchestration in service.analyse_document."""

import types
from unittest.mock import AsyncMock, patch

from app.publishing import models, service


def _finding(section: str, severity: models.SeverityLevel) -> models.AnalysisFinding:
    return models.AnalysisFinding(
        category=models.FindingCategory.HEADINGS_AND_LAYOUT,
        section=section,
        issue="An issue",
        why_it_matters="It matters",
        severity=severity,
        confidence=models.ConfidenceLevel.HIGH,
        recommendation="Fix it",
    )


def _checker_result(
    output: models.AnalysisOutput,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> types.SimpleNamespace:
    usage = (
        types.SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        if input_tokens or output_tokens
        else None
    )
    return types.SimpleNamespace(output=output, usage=usage)


def _aggregator_result(
    summary: str = "Overall summary.",
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> types.SimpleNamespace:
    usage = (
        types.SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        if input_tokens or output_tokens
        else None
    )
    return types.SimpleNamespace(
        output=models.AggregatedSummary(summary=summary), usage=usage
    )


def _sections(count: int) -> list[models.DocumentSection]:
    return [
        models.DocumentSection(number=str(n), text=f"## {n} Heading\n\nBody.\n")
        for n in range(1, count + 1)
    ]


class TestAnalyseDocument:
    async def test_runs_checker_once_per_section(self) -> None:
        outputs = [
            models.AnalysisOutput(
                findings=[],
                good_points=[],
                summary=f"Section {n}",
                verdict=models.ReadinessVerdict.READY,
            )
            for n in (1, 2, 3)
        ]

        with (
            patch(
                "app.publishing.service.checker.checker_agent.run",
                new_callable=AsyncMock,
                side_effect=[_checker_result(out) for out in outputs],
            ) as mock_checker,
            patch(
                "app.publishing.service.aggregator.aggregator_agent.run",
                new_callable=AsyncMock,
                return_value=_aggregator_result(),
            ),
        ):
            response = await service.analyse_document("Title", _sections(3))

        assert mock_checker.call_count == 3
        texts = [
            call.kwargs["deps"].document_text for call in mock_checker.call_args_list
        ]
        assert texts == [s.text for s in _sections(3)]
        assert response.status == "completed"

    async def test_merges_findings_across_sections_in_order(self) -> None:
        outputs = [
            models.AnalysisOutput(
                findings=[_finding("Section 2 Late", models.SeverityLevel.LOW)],
                good_points=["good two"],
                summary="s2",
                verdict=models.ReadinessVerdict.READY,
            ),
            models.AnalysisOutput(
                findings=[_finding("Section 1 Early", models.SeverityLevel.HIGH)],
                good_points=["good one"],
                summary="s1",
                verdict=models.ReadinessVerdict.READY,
            ),
        ]

        with (
            patch(
                "app.publishing.service.checker.checker_agent.run",
                new_callable=AsyncMock,
                side_effect=[_checker_result(out) for out in outputs],
            ),
            patch(
                "app.publishing.service.aggregator.aggregator_agent.run",
                new_callable=AsyncMock,
                return_value=_aggregator_result(),
            ),
        ):
            response = await service.analyse_document("Title", _sections(2))

        # order_findings sorts by section number, so section 1's finding leads.
        assert [f.section for f in response.findings] == [
            "Section 1 Early",
            "Section 2 Late",
        ]
        assert response.good_points == ["good two", "good one"]

    async def test_verdict_is_worst_of_sections(self) -> None:
        outputs = [
            models.AnalysisOutput(
                findings=[],
                good_points=[],
                summary="fine",
                verdict=models.ReadinessVerdict.READY,
            ),
            models.AnalysisOutput(
                findings=[],
                good_points=[],
                summary="bad",
                verdict=models.ReadinessVerdict.NOT_READY,
            ),
        ]

        with (
            patch(
                "app.publishing.service.checker.checker_agent.run",
                new_callable=AsyncMock,
                side_effect=[_checker_result(out) for out in outputs],
            ),
            patch(
                "app.publishing.service.aggregator.aggregator_agent.run",
                new_callable=AsyncMock,
                return_value=_aggregator_result(),
            ) as mock_aggregator,
        ):
            response = await service.analyse_document("Title", _sections(2))

        assert response.verdict == "not_ready"
        deps = mock_aggregator.call_args.kwargs["deps"]
        assert deps.overall_verdict == models.ReadinessVerdict.NOT_READY
        assert [s.section_number for s in deps.section_summaries] == ["1", "2"]
        assert [s.summary for s in deps.section_summaries] == ["fine", "bad"]

    async def test_title_and_summary_come_from_metadata_and_aggregator(self) -> None:
        output = models.AnalysisOutput(
            findings=[],
            good_points=[],
            summary="section summary",
            verdict=models.ReadinessVerdict.READY,
        )

        with (
            patch(
                "app.publishing.service.checker.checker_agent.run",
                new_callable=AsyncMock,
                return_value=_checker_result(output),
            ),
            patch(
                "app.publishing.service.aggregator.aggregator_agent.run",
                new_callable=AsyncMock,
                return_value=_aggregator_result(summary="The synthesized summary."),
            ),
        ):
            response = await service.analyse_document("The Real Title", _sections(1))

        assert response.document_title == "The Real Title"
        assert response.summary == "The synthesized summary."

    async def test_usage_is_summed_across_all_calls(self) -> None:
        outputs = [
            models.AnalysisOutput(
                findings=[],
                good_points=[],
                summary=f"s{n}",
                verdict=models.ReadinessVerdict.READY,
            )
            for n in (1, 2)
        ]

        with (
            patch(
                "app.publishing.service.checker.checker_agent.run",
                new_callable=AsyncMock,
                side_effect=[
                    _checker_result(outputs[0], input_tokens=100, output_tokens=10),
                    _checker_result(outputs[1], input_tokens=200, output_tokens=20),
                ],
            ),
            patch(
                "app.publishing.service.aggregator.aggregator_agent.run",
                new_callable=AsyncMock,
                return_value=_aggregator_result(input_tokens=50, output_tokens=5),
            ),
        ):
            response = await service.analyse_document("Title", _sections(2))

        assert response.usage is not None
        assert response.usage.input_tokens == 350
        assert response.usage.output_tokens == 35

    async def test_zero_sections_short_circuits_without_agent_calls(self) -> None:
        with (
            patch(
                "app.publishing.service.checker.checker_agent.run",
                new_callable=AsyncMock,
            ) as mock_checker,
            patch(
                "app.publishing.service.aggregator.aggregator_agent.run",
                new_callable=AsyncMock,
            ) as mock_aggregator,
        ):
            response = await service.analyse_document("Title", [])

        mock_checker.assert_not_called()
        mock_aggregator.assert_not_called()
        assert response.verdict == "ready"
        assert response.findings == []
        assert response.document_title == "Title"
