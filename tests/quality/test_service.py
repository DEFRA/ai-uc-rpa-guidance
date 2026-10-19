"""Tests for checking a prototype guide part by part and turning findings into comments."""

import asyncio
import contextlib
import types
import uuid
from unittest.mock import AsyncMock, patch

from app.quality import anchors, models, service

GUIDE = (
    "# Land use guide\n\n"
    "## 1 Introduction\n\nRead this first.\n\n"
    "## 2 Assign case\n\n"
    "### 2.1 Force Majeure / Obvious Error review\n\n"
    "Complete a review of CRM to confirm is any correspondence has been received.\n"
)

PARTS = anchors.top_level_parts(GUIDE)

FIVE_PARTS = anchors.top_level_parts(
    "# T\n\n" + "".join(f"## {n} Part\n\nText {n}.\n\n" for n in range(1, 6))
)


def _finding(
    exact: str, rule_id: models.RuleId = models.RuleId.GM_QUAL_24
) -> models.QualityFinding:
    return models.QualityFinding(
        rule_id=rule_id,
        exact=exact,
        issue="'is' should be 'if'.",
        why_it_matters="The error makes the instruction harder to read.",
        recommendation="Change 'is' to 'if'.",
        confidence=models.ConfidenceLevel.HIGH,
    )


def _result(
    *findings: models.QualityFinding, input_tokens: int = 0, output_tokens: int = 0
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        output=models.CheckerOutput(findings=list(findings)),
        usage=types.SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens
        ),
    )


def _patch_checker(
    *results: types.SimpleNamespace,
) -> contextlib.AbstractContextManager[AsyncMock]:
    return patch(
        "app.quality.service.checker.checker_agent.run",
        new_callable=AsyncMock,
        side_effect=list(results),
    )


class TestCheckGuide:
    async def test_runs_the_checker_once_per_top_level_part(self) -> None:
        with _patch_checker(_result(), _result()) as mock_run:
            await service.check_guide("Land use guide", PARTS)

        assert mock_run.call_count == 2
        assert [call.kwargs["deps"].part for call in mock_run.call_args_list] == PARTS

    async def test_turns_a_finding_into_a_comment(self) -> None:
        finding = _finding("confirm is any correspondence")

        with _patch_checker(_result(), _result(finding)):
            result = await service.check_guide("Land use guide", PARTS)

        [comment] = result.comments
        assert isinstance(comment.comment_id, uuid.UUID)
        assert comment.rule_id == "GM-Qual-24"
        assert comment.rule_version == 1
        assert comment.section == "2.1"
        assert comment.anchor.exact == "confirm is any correspondence"
        assert comment.anchor.prefix == "\n\nComplete a review of CRM to "
        assert comment.anchor.suffix == " has been received.\n"
        assert comment.issue == finding.issue
        assert comment.why_it_matters == finding.why_it_matters
        assert comment.recommendation == finding.recommendation
        assert comment.confidence == "high"

    async def test_every_comment_has_its_own_id(self) -> None:
        findings = [_finding("Read this"), _finding("first.")]

        with _patch_checker(_result(*findings), _result()):
            result = await service.check_guide("Land use guide", PARTS)

        assert len({comment.comment_id for comment in result.comments}) == 2

    async def test_keeps_the_comments_in_document_order(self) -> None:
        with _patch_checker(
            _result(_finding("first."), _finding("Read this")),
            _result(_finding("confirm is any correspondence")),
        ):
            result = await service.check_guide("Land use guide", PARTS)

        assert [comment.anchor.exact for comment in result.comments] == [
            "Read this",
            "first.",
            "confirm is any correspondence",
        ]

    async def test_adds_up_the_tokens_used(self) -> None:
        with _patch_checker(
            _result(input_tokens=100, output_tokens=10),
            _result(input_tokens=200, output_tokens=20),
        ):
            result = await service.check_guide("Land use guide", PARTS)

        assert result.usage.input_tokens == 300
        assert result.usage.output_tokens == 30

    async def test_a_guide_with_no_parts_has_no_comments(self) -> None:
        with _patch_checker() as mock_run:
            result = await service.check_guide("Empty", [])

        mock_run.assert_not_called()
        assert result.comments == []
        assert result.document_title == "Empty"


class TestConcurrency:
    async def test_checks_one_part_at_a_time(self) -> None:
        running = 0
        most_running = 0

        async def _run(*_args: object, **_kwargs: object) -> types.SimpleNamespace:
            nonlocal running, most_running
            running += 1
            most_running = max(most_running, running)
            await asyncio.sleep(0.01)
            running -= 1
            return _result()

        with patch(
            "app.quality.service.checker.checker_agent.run",
            new_callable=AsyncMock,
            side_effect=_run,
        ):
            await service.check_guide("T", FIVE_PARTS)

        assert most_running == 1

    async def test_keeps_document_order_when_a_later_part_finishes_first(
        self,
    ) -> None:
        async def _run(
            *_args: object, deps: models.AgentDependencies, **_kwargs: object
        ) -> types.SimpleNamespace:
            label = deps.part.label
            await asyncio.sleep(0.05 if label == "1" else 0)
            return _result(_finding(f"Text {label}."))

        with patch(
            "app.quality.service.checker.checker_agent.run",
            new_callable=AsyncMock,
            side_effect=_run,
        ):
            result = await service.check_guide("T", FIVE_PARTS)

        assert [comment.section for comment in result.comments] == [
            "1",
            "2",
            "3",
            "4",
            "5",
        ]


class TestProgress:
    async def test_reports_each_part_as_it_finishes(self) -> None:
        done: list[str] = []

        async def _part_done() -> None:
            done.append("part")

        with _patch_checker(_result(), _result()):
            await service.check_guide("Land use guide", PARTS, on_part_done=_part_done)

        assert done == ["part", "part"]
