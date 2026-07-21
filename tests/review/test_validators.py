"""Tests for the reviewer agent output validator (quote anchoring)."""

import dataclasses

import pydantic_ai
import pytest

from app.review import models
from app.review.agents import reviewer

DOCUMENT = """# Guide

## Step 1

The form should be completed by the case worker.

![screenshot](../images/img_1.png)

See the [navigation guide](https://example.com/nav.docx) for details.
"""


HTML_DOCUMENT = (
    "## Decision\n\n"
    "<strong>No</strong>, you must contact the agreement holder to query "
    "it&#x27;s status.\n\n"
    '<a href="https://ex/1">Navigate to the</a><a href="https://ex/1"> </a>'
    'SFI <a href="https://ex/1">Basic Navigation</a> section.\n'
)

TYPO_DOCUMENT = "Check the ‘Link’ tab — then continue.\n"


@dataclasses.dataclass
class StubRunContext:
    deps: models.AgentDependencies
    last_attempt: bool = False


def make_deps(document_text: str = DOCUMENT) -> models.AgentDependencies:
    return models.AgentDependencies(document_text=document_text)


def make_finding(quote: str) -> models.ReviewFinding:
    return models.ReviewFinding(
        principle=models.Principle.PLAIN_ENGLISH,
        section="Step 1",
        quote=quote,
        issue="Passive voice",
        why_it_matters="Harder to follow on first read",
        severity=models.SeverityLevel.MEDIUM,
        confidence=models.ConfidenceLevel.HIGH,
        recommendation="Rewrite in active voice",
    )


def make_good_point(quote: str) -> models.GoodPoint:
    return models.GoodPoint(
        principle=models.Principle.SCAN_FRIENDLY,
        quote=quote,
        comment="Clear and scannable",
    )


def make_output(
    findings: list[models.ReviewFinding],
    good_points: list[models.GoodPoint] | None = None,
) -> models.ReviewOutput:
    ratings = models.PrincipleRatings(
        **{
            name: models.PrincipleRating(
                justification="j", rating=models.RatingLevel.PARTLY_APPLIED
            )
            for name in models.PrincipleRatings.model_fields
        }
    )
    return models.ReviewOutput(
        document_title="Guide",
        task_context=models.TaskContext(task="t", user="u", usage_context="c"),
        good_points=good_points or [],
        findings=findings,
        principle_ratings=ratings,
        usability=models.UsabilityAssessment(
            explanation="e", verdict=models.UsabilityVerdict.PARTLY
        ),
    )


class TestReviewerQuoteValidator:
    async def test_verbatim_quote_passes(self) -> None:
        output = make_output(
            [make_finding("The form should be completed by the case worker")]
        )

        result = await reviewer.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps()), output
        )

        assert result is output

    async def test_quote_with_different_wrapping_passes(self) -> None:
        output = make_output(
            [make_finding("The form should be\ncompleted by   the case worker")]
        )

        result = await reviewer.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps()), output
        )

        assert result is output

    async def test_paraphrased_quote_is_rejected(self) -> None:
        output = make_output([make_finding("The case worker should complete the form")])

        with pytest.raises(pydantic_ai.ModelRetry) as exc_info:
            await reviewer.validate_quotes_are_verbatim(
                StubRunContext(deps=make_deps()), output
            )

        assert "verbatim" in str(exc_info.value)
        assert "case worker should complete" in str(exc_info.value)

    async def test_only_unanchored_findings_are_reported(self) -> None:
        output = make_output(
            [
                make_finding("The form should be completed by the case worker"),
                make_finding("invented text"),
            ]
        )

        with pytest.raises(pydantic_ai.ModelRetry) as exc_info:
            await reviewer.validate_quotes_are_verbatim(
                StubRunContext(deps=make_deps()), output
            )

        message = str(exc_info.value)
        assert "invented text" in message
        assert "form should be completed" not in message

    async def test_no_findings_passes(self) -> None:
        output = make_output([])

        result = await reviewer.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps()), output
        )

        assert result is output

    async def test_html_tags_and_entities_are_stripped_for_anchoring(self) -> None:
        # The clean quote spans a <strong> boundary and an escaped apostrophe.
        output = make_output(
            [
                make_finding(
                    "No, you must contact the agreement holder to query it's status."
                )
            ]
        )

        result = await reviewer.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps(HTML_DOCUMENT)), output
        )

        assert result is output

    async def test_fragmented_link_text_anchors(self) -> None:
        # In the document this sentence is shattered across several <a> tags.
        output = make_output(
            [make_finding("Navigate to the SFI Basic Navigation section.")]
        )

        result = await reviewer.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps(HTML_DOCUMENT)), output
        )

        assert result is output

    async def test_smart_typography_is_normalised(self) -> None:
        # Straight quotes / hyphen quote against curly quotes / em dash source.
        output = make_output([make_finding("Check the 'Link' tab - then continue.")])

        result = await reviewer.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps(TYPO_DOCUMENT)), output
        )

        assert result is output

    async def test_unanchored_findings_dropped_on_last_attempt(self) -> None:
        anchored = make_finding("The form should be completed by the case worker")
        unanchored = make_finding("invented text")
        output = make_output([anchored, unanchored])

        result = await reviewer.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps(), last_attempt=True), output
        )

        assert result is not output
        assert result.findings == [anchored]


class TestGoodPointQuoteValidator:
    async def test_anchored_good_point_passes(self) -> None:
        output = make_output(
            [],
            good_points=[
                make_good_point("The form should be completed by the case worker.")
            ],
        )

        result = await reviewer.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps()), output
        )

        assert result is output

    async def test_unanchored_good_point_is_rejected(self) -> None:
        output = make_output([], good_points=[make_good_point("invented praise")])

        with pytest.raises(pydantic_ai.ModelRetry) as exc_info:
            await reviewer.validate_quotes_are_verbatim(
                StubRunContext(deps=make_deps()), output
            )

        assert "invented praise" in str(exc_info.value)

    async def test_unanchored_good_point_dropped_on_last_attempt(self) -> None:
        anchored_finding = make_finding(
            "The form should be completed by the case worker"
        )
        good = make_good_point("The form should be completed by the case worker.")
        bad = make_good_point("invented praise")
        output = make_output([anchored_finding], good_points=[good, bad])

        result = await reviewer.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps(), last_attempt=True), output
        )

        assert result is not output
        assert result.findings == [anchored_finding]
        assert result.good_points == [good]
