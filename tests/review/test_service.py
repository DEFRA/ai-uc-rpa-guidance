"""Tests for the guidance review service."""

from unittest.mock import AsyncMock, Mock, patch

from app.review import models, service


def _make_ratings() -> models.PrincipleRatings:
    values = {
        name: models.PrincipleRating(
            justification=f"justification for {name}",
            rating=models.RatingLevel.PARTLY_APPLIED,
        )
        for name in models.PrincipleRatings.model_fields
    }
    return models.PrincipleRatings(**values)


def _make_finding(
    section: str = "Step 1",
    severity: models.SeverityLevel = models.SeverityLevel.MEDIUM,
) -> models.ReviewFinding:
    return models.ReviewFinding(
        principle=models.Principle.DECISION_LED,
        section=section,
        quote="the text",
        issue="No if/then logic",
        why_it_matters="Users cannot decide what to do",
        severity=severity,
        confidence=models.ConfidenceLevel.HIGH,
        recommendation="Add if/then decisions",
    )


def _make_output(
    findings: list[models.ReviewFinding] | None = None,
) -> models.ReviewOutput:
    return models.ReviewOutput(
        document_title="Test Guidance",
        task_context=models.TaskContext(
            task="Process a claim",
            user="A claims processor",
            usage_context="Used live on calls, under time pressure",
        ),
        good_points=[
            models.GoodPoint(
                principle=models.Principle.SCAN_FRIENDLY,
                quote="Step 1: open the case",
                comment="Clear action-led step",
            )
        ],
        findings=findings or [],
        principle_ratings=_make_ratings(),
        usability=models.UsabilityAssessment(
            explanation="Key decisions are unclear",
            verdict=models.UsabilityVerdict.PARTLY,
        ),
    )


class TestReviewDocument:
    async def test_maps_output_to_response(self) -> None:
        output = _make_output(findings=[_make_finding()])
        mock_result = Mock()
        mock_result.output = output
        mock_result.usage = None

        with patch(
            "app.review.service.reviewer.reviewer_agent.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = await service.review_document("# Doc")

        assert response.status == "completed"
        assert response.document_title == "Test Guidance"
        assert response.task_context.task == "Process a claim"
        assert response.task_context.user == "A claims processor"
        assert response.usability.verdict == "partly"
        assert response.usability.explanation == "Key decisions are unclear"
        assert len(response.good_points) == 1
        assert response.good_points[0].principle == "scan_friendly"
        assert response.good_points[0].quote == "Step 1: open the case"
        assert len(response.findings) == 1
        finding = response.findings[0]
        assert finding.principle == "decision_led"
        assert finding.severity == "medium"
        assert finding.confidence == "high"
        assert finding.quote == "the text"
        assert response.usage is None

    async def test_maps_all_principle_ratings(self) -> None:
        output = _make_output()
        mock_result = Mock()
        mock_result.output = output
        mock_result.usage = None

        with patch(
            "app.review.service.reviewer.reviewer_agent.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = await service.review_document("# Doc")

        ratings = response.principle_ratings
        for name in models.PrincipleRatings.model_fields:
            assert getattr(ratings, name) == "partly_applied"

    async def test_findings_are_reordered_for_presentation(self) -> None:
        output = _make_output(
            findings=[
                _make_finding(section="Section 10 Closure"),
                _make_finding(section="Section 2 Find"),
            ]
        )
        mock_result = Mock()
        mock_result.output = output
        mock_result.usage = None

        with patch(
            "app.review.service.reviewer.reviewer_agent.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = await service.review_document("# Doc")

        assert [finding.section for finding in response.findings] == [
            "Section 2 Find",
            "Section 10 Closure",
        ]

    async def test_usage_is_mapped_when_present(self) -> None:
        usage = Mock()
        usage.input_tokens = 1200
        usage.output_tokens = 340
        mock_result = Mock()
        mock_result.output = _make_output()
        mock_result.usage = usage

        with patch(
            "app.review.service.reviewer.reviewer_agent.run",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = await service.review_document("# Doc")

        assert response.usage is not None
        assert response.usage.input_tokens == 1200
        assert response.usage.output_tokens == 340
