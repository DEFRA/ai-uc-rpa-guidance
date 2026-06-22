"""Tests for the critic -> writer -> critic orchestration loop."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_mock

from app.critique import models, service


def make_critique(
    approved: bool,
    findings: list[models.CritiqueFinding] | None = None,
    summary: str = "Review summary",
    conformance: list[models.ConformanceSummary] | None = None,
) -> models.CritiqueOutput:
    return models.CritiqueOutput(
        approved=approved,
        findings=findings or [],
        conformance=conformance or [],
        summary=summary,
    )


def make_finding(
    standard: models.Standard = models.Standard.GDS,
    what: str = "Passive voice",
) -> models.CritiqueFinding:
    return models.CritiqueFinding(
        standard=standard,
        rule_reference="Active voice",
        what=what,
        where="Introduction",
        quote="The form should be completed by the case worker",
        why="The style guide requires active voice",
        fix="Rewrite in active voice",
        severity=models.SeverityLevel.MEDIUM,
    )


def make_run_result(output: object, input_tokens: int = 10, output_tokens: int = 5):
    result = MagicMock()
    result.output = output
    result.usage.input_tokens = input_tokens
    result.usage.output_tokens = output_tokens
    return result


def make_revision(revised_document: str) -> models.RevisionOutput:
    return models.RevisionOutput(
        revised_document=revised_document, change_notes="Applied findings"
    )


@pytest.fixture
def critic_run(mocker: pytest_mock.MockerFixture) -> AsyncMock:
    return mocker.patch("app.critique.service.critic.critic_agent.run")


@pytest.fixture
def writer_run(mocker: pytest_mock.MockerFixture) -> AsyncMock:
    return mocker.patch("app.critique.service.writer.writer_agent.run")


class TestApprovedFirstPass:
    async def test_writer_not_called_when_approved_immediately(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        critic_run.return_value = make_run_result(make_critique(approved=True))

        result = await service.critique_document("# Doc", revise=True)

        assert result.status == "approved"
        assert result.iterations == 1
        writer_run.assert_not_called()

    async def test_no_revised_document_when_approved_unrevised(
        self, critic_run: AsyncMock
    ) -> None:
        critic_run.return_value = make_run_result(make_critique(approved=True))

        result = await service.critique_document("# Doc", revise=True)

        assert result.revised_document is None
        assert result.invariant_warnings == []


class TestCritiqueOnly:
    """revise=False (the default): single critic pass, no writer."""

    async def test_writer_never_called_despite_findings(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        critic_run.return_value = make_run_result(
            make_critique(approved=False, findings=[make_finding()])
        )

        result = await service.critique_document("# Doc")

        assert result.status == "review_completed"
        assert result.iterations == 1
        assert result.revised_document is None
        writer_run.assert_not_called()
        critic_run.assert_called_once()

    async def test_reports_still_built(self, critic_run: AsyncMock) -> None:
        critic_run.return_value = make_run_result(
            make_critique(approved=False, findings=[make_finding()])
        )

        result = await service.critique_document("# Doc")

        assert [r.standard for r in result.reports] == ["gds", "defra_style"]
        assert sum(len(r.findings) for r in result.reports) == 1

    async def test_approved_status_when_no_findings(
        self, critic_run: AsyncMock
    ) -> None:
        critic_run.return_value = make_run_result(make_critique(approved=True))

        result = await service.critique_document("# Doc")

        assert result.status == "approved"
        assert result.revised_document is None

    async def test_max_iterations_ignored_without_revise(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        critic_run.return_value = make_run_result(
            make_critique(approved=False, findings=[make_finding()])
        )

        result = await service.critique_document("# Doc", max_iterations=3)

        assert result.iterations == 1
        writer_run.assert_not_called()


class TestApprovedAfterRevision:
    async def test_revision_loop_runs_and_returns_revised_document(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        critic_run.side_effect = [
            make_run_result(make_critique(approved=False, findings=[make_finding()])),
            make_run_result(make_critique(approved=True)),
        ]
        writer_run.return_value = make_run_result(make_revision("# Doc revised"))

        result = await service.critique_document("# Doc", revise=True)

        assert result.status == "approved"
        assert result.iterations == 2
        assert result.revised_document == "# Doc revised"
        assert writer_run.call_count == 1

    async def test_second_critic_pass_receives_previous_findings(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        finding = make_finding()
        critic_run.side_effect = [
            make_run_result(make_critique(approved=False, findings=[finding])),
            make_run_result(make_critique(approved=True)),
        ]
        writer_run.return_value = make_run_result(make_revision("# Doc revised"))

        await service.critique_document("# Doc", revise=True)

        second_call_deps = critic_run.call_args_list[1].kwargs["deps"]
        assert second_call_deps.previous_findings == [finding]
        assert second_call_deps.document_text == "# Doc revised"

    async def test_writer_receives_critic_findings(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        finding = make_finding()
        critic_run.side_effect = [
            make_run_result(make_critique(approved=False, findings=[finding])),
            make_run_result(make_critique(approved=True)),
        ]
        writer_run.return_value = make_run_result(make_revision("# Doc revised"))

        await service.critique_document("# Doc", revise=True)

        writer_deps = writer_run.call_args.kwargs["deps"]
        assert writer_deps.findings_to_apply == [finding]


class TestIterationCap:
    async def test_cap_reached_returns_max_iterations_status(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        critic_run.return_value = make_run_result(
            make_critique(approved=False, findings=[make_finding()])
        )
        writer_run.return_value = make_run_result(make_revision("# Doc revised"))

        result = await service.critique_document("# Doc", revise=True)

        assert result.status == "max_iterations_reached"
        assert result.iterations == 3  # config default
        assert writer_run.call_count == 2  # no revision after the final critique

    async def test_requested_cap_lower_than_config_is_respected(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        critic_run.return_value = make_run_result(
            make_critique(approved=False, findings=[make_finding()])
        )
        writer_run.return_value = make_run_result(make_revision("# Doc revised"))

        result = await service.critique_document("# Doc", max_iterations=1, revise=True)

        assert result.iterations == 1
        writer_run.assert_not_called()

    async def test_requested_cap_above_config_is_bounded(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        critic_run.return_value = make_run_result(
            make_critique(approved=False, findings=[make_finding()])
        )
        writer_run.return_value = make_run_result(make_revision("# Doc revised"))

        result = await service.critique_document(
            "# Doc", max_iterations=99, revise=True
        )

        assert result.iterations == 3  # config default is the upper bound


class TestReports:
    async def test_reports_built_from_first_critique_grouped_by_standard(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        gds_finding = make_finding(standard=models.Standard.GDS, what="Passive voice")
        defra_finding = make_finding(
            standard=models.Standard.DEFRA_STYLE, what="Wrong term"
        )
        critic_run.side_effect = [
            make_run_result(
                make_critique(
                    approved=False,
                    findings=[gds_finding, defra_finding],
                    conformance=[
                        models.ConformanceSummary(
                            standard=models.Standard.GDS, summary="Dates conform"
                        ),
                        models.ConformanceSummary(
                            standard=models.Standard.DEFRA_STYLE,
                            summary="Terminology mostly conforms",
                        ),
                    ],
                )
            ),
            make_run_result(make_critique(approved=True)),
        ]
        writer_run.return_value = make_run_result(make_revision("# Doc revised"))

        result = await service.critique_document("# Doc", revise=True)

        assert [r.standard for r in result.reports] == ["gds", "defra_style"]
        gds_report, defra_report = result.reports
        assert gds_report.conformance_summary == "Dates conform"
        assert [f.what for f in gds_report.findings] == ["Passive voice"]
        assert [f.what for f in defra_report.findings] == ["Wrong term"]

    async def test_reports_reflect_first_pass_not_later_passes(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        critic_run.side_effect = [
            make_run_result(make_critique(approved=False, findings=[make_finding()])),
            make_run_result(make_critique(approved=True, summary="All good now")),
        ]
        writer_run.return_value = make_run_result(make_revision("# Doc revised"))

        result = await service.critique_document("# Doc", revise=True)

        total_report_findings = sum(len(r.findings) for r in result.reports)
        assert total_report_findings == 1
        assert [h.finding_count for h in result.critique_history] == [1, 0]


class TestUsageAndInvariants:
    async def test_usage_accumulates_across_all_runs(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        critic_run.side_effect = [
            make_run_result(
                make_critique(approved=False, findings=[make_finding()]),
                input_tokens=100,
                output_tokens=10,
            ),
            make_run_result(
                make_critique(approved=True), input_tokens=120, output_tokens=12
            ),
        ]
        writer_run.return_value = make_run_result(
            make_revision("# Doc revised"), input_tokens=200, output_tokens=50
        )

        result = await service.critique_document("# Doc", revise=True)

        assert result.usage is not None
        assert result.usage.input_tokens == 420
        assert result.usage.output_tokens == 72

    async def test_invariant_warning_when_revision_drops_image(
        self, critic_run: AsyncMock, writer_run: AsyncMock
    ) -> None:
        original = "# Doc\n\n![shot](img.png)\n"
        critic_run.side_effect = [
            make_run_result(make_critique(approved=False, findings=[make_finding()])),
            make_run_result(make_critique(approved=True)),
        ]
        writer_run.return_value = make_run_result(make_revision("# Doc\n"))

        result = await service.critique_document(original, revise=True)

        assert any("img.png" in w for w in result.invariant_warnings)
