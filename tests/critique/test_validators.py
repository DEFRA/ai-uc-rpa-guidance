"""Tests for the agent output validators (quote anchoring, AC5 preservation)."""

import dataclasses

import pydantic_ai
import pytest

from app.critique import models
from app.critique.agents import critic, writer

DOCUMENT = """# Guide

## Step 1

The form should be completed by the case worker.

![screenshot](../images/img_1.png)

See the [navigation guide](https://example.com/nav.docx) for details.
"""


@dataclasses.dataclass
class StubRunContext:
    deps: models.AgentDependencies


def make_deps(document_text: str = DOCUMENT) -> models.AgentDependencies:
    return models.AgentDependencies(document_text=document_text)


def make_finding(quote: str) -> models.CritiqueFinding:
    return models.CritiqueFinding(
        standard=models.Standard.GDS,
        rule_reference="Active voice",
        what="Passive voice",
        where="Step 1",
        quote=quote,
        why="The style guide requires active voice",
        fix="Rewrite in active voice",
        severity=models.SeverityLevel.MEDIUM,
    )


def make_critique(findings: list[models.CritiqueFinding]) -> models.CritiqueOutput:
    return models.CritiqueOutput(
        approved=not findings, findings=findings, conformance=[], summary="s"
    )


class TestCriticQuoteValidator:
    async def test_verbatim_quote_passes(self) -> None:
        output = make_critique(
            [make_finding("The form should be completed by the case worker")]
        )

        result = await critic.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps()), output
        )

        assert result is output

    async def test_quote_with_different_wrapping_passes(self) -> None:
        output = make_critique(
            [make_finding("The form should be\ncompleted by   the case worker")]
        )

        result = await critic.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps()), output
        )

        assert result is output

    async def test_paraphrased_quote_is_rejected(self) -> None:
        output = make_critique(
            [make_finding("The case worker should complete the form")]
        )

        with pytest.raises(pydantic_ai.ModelRetry) as exc_info:
            await critic.validate_quotes_are_verbatim(
                StubRunContext(deps=make_deps()), output
            )

        assert "verbatim" in str(exc_info.value)
        assert "case worker should complete" in str(exc_info.value)

    async def test_only_unanchored_findings_are_reported(self) -> None:
        output = make_critique(
            [
                make_finding("The form should be completed by the case worker"),
                make_finding("invented text"),
            ]
        )

        with pytest.raises(pydantic_ai.ModelRetry) as exc_info:
            await critic.validate_quotes_are_verbatim(
                StubRunContext(deps=make_deps()), output
            )

        message = str(exc_info.value)
        assert "invented text" in message
        assert "form should be completed" not in message

    async def test_no_findings_passes(self) -> None:
        output = make_critique([])

        result = await critic.validate_quotes_are_verbatim(
            StubRunContext(deps=make_deps()), output
        )

        assert result is output


class TestWriterPreservationValidator:
    async def test_text_level_revision_passes(self) -> None:
        revision = models.RevisionOutput(
            revised_document=DOCUMENT.replace(
                "The form should be completed by the case worker",
                "Complete the form",
            ),
            change_notes="active voice",
        )

        result = await writer.validate_structure_preserved(
            StubRunContext(deps=make_deps()), revision
        )

        assert result is revision

    async def test_dropped_image_is_rejected(self) -> None:
        revision = models.RevisionOutput(
            revised_document=DOCUMENT.replace(
                "![screenshot](../images/img_1.png)\n\n", ""
            ),
            change_notes="oops",
        )

        with pytest.raises(pydantic_ai.ModelRetry) as exc_info:
            await writer.validate_structure_preserved(
                StubRunContext(deps=make_deps()), revision
            )

        assert "img_1.png" in str(exc_info.value)

    async def test_dropped_link_is_rejected(self) -> None:
        revision = models.RevisionOutput(
            revised_document=DOCUMENT.replace(
                "[navigation guide](https://example.com/nav.docx)", "navigation guide"
            ),
            change_notes="oops",
        )

        with pytest.raises(pydantic_ai.ModelRetry):
            await writer.validate_structure_preserved(
                StubRunContext(deps=make_deps()), revision
            )

    async def test_changed_heading_structure_is_rejected(self) -> None:
        revision = models.RevisionOutput(
            revised_document=DOCUMENT.replace("## Step 1", "### Step 1"),
            change_notes="oops",
        )

        with pytest.raises(pydantic_ai.ModelRetry):
            await writer.validate_structure_preserved(
                StubRunContext(deps=make_deps()), revision
            )
