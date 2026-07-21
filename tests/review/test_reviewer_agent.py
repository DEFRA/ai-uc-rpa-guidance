"""Tests for the guidance reviewer agent and models."""

import dataclasses

import pydantic
import pytest

import app.review.models as models
from app.review.agents import reviewer


def make_ratings(
    rating: models.RatingLevel = models.RatingLevel.PARTLY_APPLIED,
) -> models.PrincipleRatings:
    values = {
        name: models.PrincipleRating(justification="j", rating=rating)
        for name in models.PrincipleRatings.model_fields
    }
    return models.PrincipleRatings(**values)


def make_finding() -> models.ReviewFinding:
    return models.ReviewFinding(
        principle=models.Principle.PLAIN_ENGLISH,
        section="Introduction",
        quote="the text",
        issue="Text is unclear",
        why_it_matters="Users cannot complete the task first time",
        severity=models.SeverityLevel.MEDIUM,
        confidence=models.ConfidenceLevel.HIGH,
        recommendation="Rewrite in plain English",
    )


class TestReviewFinding:
    """Test ReviewFinding model validation."""

    def test_valid_finding(self) -> None:
        """Test creating a valid review finding."""
        finding = make_finding()
        assert finding.principle == models.Principle.PLAIN_ENGLISH
        assert finding.section == "Introduction"
        assert finding.severity == models.SeverityLevel.MEDIUM
        assert finding.confidence == models.ConfidenceLevel.HIGH

    def test_finding_severity_values(self) -> None:
        """Test all severity level values are valid."""
        assert models.SeverityLevel.INFO == "info"
        assert models.SeverityLevel.LOW == "low"
        assert models.SeverityLevel.MEDIUM == "medium"
        assert models.SeverityLevel.HIGH == "high"
        assert models.SeverityLevel.CRITICAL == "critical"

    def test_principle_values(self) -> None:
        """Test all principle values are valid."""
        assert models.Principle.CLEAR_PURPOSE == "clear_purpose"
        assert models.Principle.STARTS_WITH_THE_READER == "starts_with_the_reader"
        assert models.Principle.TASK_FOCUSED_STRUCTURE == "task_focused_structure"
        assert models.Principle.PLAIN_ENGLISH == "plain_english"
        assert models.Principle.MULTIPLE_FORMATS == "multiple_formats"
        assert models.Principle.DECISION_LED == "decision_led"
        assert models.Principle.SCAN_FRIENDLY == "scan_friendly"
        assert models.Principle.ACCESSIBLE_BY_DEFAULT == "accessible_by_default"
        assert models.Principle.CONSISTENT == "consistent"
        assert models.Principle.USABLE_UNDER_PRESSURE == "usable_under_pressure"

    def test_rating_level_values(self) -> None:
        """Test all rating level values are valid."""
        assert models.RatingLevel.FULLY_APPLIED == "fully_applied"
        assert models.RatingLevel.PARTLY_APPLIED == "partly_applied"
        assert models.RatingLevel.NOT_APPLIED == "not_applied"

    def test_usability_verdict_values(self) -> None:
        """Test all usability verdict values are valid."""
        assert models.UsabilityVerdict.YES == "yes"
        assert models.UsabilityVerdict.PARTLY == "partly"
        assert models.UsabilityVerdict.NO == "no"

    def test_finding_rejects_empty_quote(self) -> None:
        """A finding must anchor to a non-empty quote."""
        with pytest.raises(pydantic.ValidationError):
            models.ReviewFinding(
                principle=models.Principle.PLAIN_ENGLISH,
                section="Introduction",
                quote="",
                issue="Text is unclear",
                why_it_matters="why",
                severity=models.SeverityLevel.MEDIUM,
                confidence=models.ConfidenceLevel.HIGH,
                recommendation="fix",
            )

    def test_finding_generates_quote_before_issue(self) -> None:
        """quote must precede issue so the LLM anchors evidence first."""
        fields = list(models.ReviewFinding.model_fields)
        assert fields.index("quote") < fields.index("issue")

    def test_finding_generates_reasoning_before_severity(self) -> None:
        """why_it_matters must precede severity so the LLM reasons first."""
        fields = list(models.ReviewFinding.model_fields)
        assert fields.index("why_it_matters") < fields.index("severity")


class TestGoodPoint:
    """Test GoodPoint model validation."""

    def test_valid_good_point(self) -> None:
        point = models.GoodPoint(
            principle=models.Principle.SCAN_FRIENDLY,
            quote="Step 1: open the case",
            comment="Clear action-led step",
        )
        assert point.principle == models.Principle.SCAN_FRIENDLY

    def test_good_point_rejects_empty_quote(self) -> None:
        """A good point must anchor to a non-empty quote."""
        with pytest.raises(pydantic.ValidationError):
            models.GoodPoint(
                principle=models.Principle.SCAN_FRIENDLY,
                quote="",
                comment="Clear action-led step",
            )


class TestPrincipleRatings:
    """Test PrincipleRatings model."""

    def test_fields_match_principles(self) -> None:
        """Every principle must be rated; the fields must match the enum."""
        fields = set(models.PrincipleRatings.model_fields)
        principles = {principle.value for principle in models.Principle}
        assert fields == principles

    def test_rating_justifies_before_rating(self) -> None:
        """justification must precede rating so the LLM reasons first."""
        fields = list(models.PrincipleRating.model_fields)
        assert fields.index("justification") < fields.index("rating")

    def test_all_principles_must_be_rated(self) -> None:
        """Omitting a principle is a validation error."""
        values = {
            name: models.PrincipleRating(
                justification="j", rating=models.RatingLevel.FULLY_APPLIED
            )
            for name in models.PrincipleRatings.model_fields
        }
        values.pop("decision_led")
        with pytest.raises(pydantic.ValidationError):
            models.PrincipleRatings(**values)


class TestUsabilityAssessment:
    """Test the reason-before-verdict ordering of the usability model."""

    def test_usability_explains_before_verdict(self) -> None:
        fields = list(models.UsabilityAssessment.model_fields)
        assert fields.index("explanation") < fields.index("verdict")


class TestReviewOutput:
    """Test ReviewOutput model validation."""

    def test_valid_output_with_findings(self) -> None:
        """Test creating a valid review output with findings."""
        output = models.ReviewOutput(
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
            findings=[make_finding()],
            principle_ratings=make_ratings(),
            usability=models.UsabilityAssessment(
                explanation="Key decisions are unclear",
                verdict=models.UsabilityVerdict.PARTLY,
            ),
        )
        assert output.usability.verdict == models.UsabilityVerdict.PARTLY
        assert len(output.findings) == 1
        assert len(output.good_points) == 1

    def test_output_with_empty_feedback(self) -> None:
        """Test creating output with no findings or good points."""
        output = models.ReviewOutput(
            document_title="Test Guidance",
            task_context=models.TaskContext(
                task="Process a claim",
                user="A claims processor",
                usage_context="Used live on calls",
            ),
            principle_ratings=make_ratings(models.RatingLevel.FULLY_APPLIED),
            usability=models.UsabilityAssessment(
                explanation="Fully supports the task",
                verdict=models.UsabilityVerdict.YES,
            ),
        )
        assert output.findings == []
        assert output.good_points == []

    def test_output_generates_ratings_after_findings(self) -> None:
        """Ratings must come after the evidence they are conditioned on."""
        fields = list(models.ReviewOutput.model_fields)
        assert fields.index("principle_ratings") > fields.index("findings")
        assert fields.index("principle_ratings") > fields.index("good_points")

    def test_output_generates_usability_last(self) -> None:
        """usability must be the final field so the verdict comes last."""
        fields = list(models.ReviewOutput.model_fields)
        assert fields[-1] == "usability"


class FakePromptRepository:
    """Returns a fixed body and records the names it was asked for."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.requested: list[str] = []

    async def get_prompt_by_name(self, name: str) -> str:
        self.requested.append(name)
        return self.body


@dataclasses.dataclass
class StubRunContext:
    deps: models.AgentDependencies


class TestReviewerInstructions:
    """The reviewer agent's instruction builder."""

    async def test_instructions_loaded_from_repository(self) -> None:
        """Instructions are the repository prompt with the document appended."""
        repo = FakePromptRepository("SYSTEM PROMPT")
        deps = models.AgentDependencies(
            document_text="# The Document",
            prompt_repository=repo,
        )

        result = await reviewer.get_instructions(StubRunContext(deps=deps))

        assert repo.requested == ["reviewer_v1.md"]
        assert result == "SYSTEM PROMPT\n\n# The Document"
