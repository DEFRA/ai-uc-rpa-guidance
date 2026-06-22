"""Tests for the critic/writer agent instruction builders."""

import dataclasses

import pytest

from app.critique import models
from app.critique.agents import critic, writer
from tests.critique import fakes


class FakePromptRepository:
    """Returns a fixed prompt body for any name."""

    def __init__(self, prompts: dict[str, str]) -> None:
        self.prompts = prompts

    async def get_prompt_by_name(self, name: str) -> str:
        return self.prompts[name]


@dataclasses.dataclass
class StubRunContext:
    deps: models.AgentDependencies


def make_finding() -> models.CritiqueFinding:
    return models.CritiqueFinding(
        standard=models.Standard.GDS,
        rule_reference="Active voice",
        what="Passive voice",
        where="Introduction",
        quote="The form should be completed by the case worker",
        why="The style guide requires active voice",
        fix="Rewrite in active voice",
        severity=models.SeverityLevel.MEDIUM,
    )


@pytest.fixture
def context_repository() -> fakes.FakeContextRepository:
    return fakes.FakeContextRepository(
        {
            "content-style-guide/index.json": (
                '[{"title": "Dates", "file": "content-style-guide/dates.md"}]'
            ),
            "content-guidance/index.json": (
                '[{"title": "Clear language", "file": "content-guidance/clear-language.md"}]'
            ),
            "defra-style-guide/index.json": (
                '[{"title": "Defra style guide — A", "file": "defra-style-guide/a.md"}]'
            ),
        }
    )


def make_deps(
    context_repository: fakes.FakeContextRepository,
    prompt: str,
    prompt_name: str,
    **kwargs: object,
) -> models.AgentDependencies:
    return models.AgentDependencies(
        document_text="# The Document",
        prompt_repository=FakePromptRepository({prompt_name: prompt}),
        context_repository=context_repository,
        **kwargs,  # type: ignore[arg-type]
    )


class TestCriticInstructions:
    async def test_includes_prompt_document_and_inlined_catalogues(
        self, context_repository: fakes.FakeContextRepository
    ) -> None:
        deps = make_deps(context_repository, "CRITIC PROMPT", "critic.md")

        result = await critic.get_instructions(StubRunContext(deps=deps))

        assert result.startswith("CRITIC PROMPT")
        assert "# The Document" in result
        assert "- Dates — file: content-style-guide/dates.md" in result
        assert "- Clear language — file: content-guidance/clear-language.md" in result
        assert "- Defra style guide — A — file: defra-style-guide/a.md" in result

    async def test_no_previous_findings_section_on_first_pass(
        self, context_repository: fakes.FakeContextRepository
    ) -> None:
        deps = make_deps(context_repository, "CRITIC PROMPT", "critic.md")

        result = await critic.get_instructions(StubRunContext(deps=deps))

        assert "Previous review findings" not in result

    async def test_previous_findings_included_on_re_review(
        self, context_repository: fakes.FakeContextRepository
    ) -> None:
        deps = make_deps(
            context_repository,
            "CRITIC PROMPT",
            "critic.md",
            previous_findings=[make_finding()],
        )

        result = await critic.get_instructions(StubRunContext(deps=deps))

        assert "Previous review findings" in result
        assert "Rewrite in active voice" in result

    async def test_missing_index_falls_back_gracefully(self) -> None:
        deps = make_deps(fakes.FakeContextRepository(), "CRITIC PROMPT", "critic.md")

        result = await critic.get_instructions(StubRunContext(deps=deps))

        assert "catalogue unavailable" in result
        assert "# The Document" in result


class TestWriterInstructions:
    async def test_includes_prompt_document_and_findings(
        self, context_repository: fakes.FakeContextRepository
    ) -> None:
        deps = make_deps(
            context_repository,
            "WRITER PROMPT",
            "writer.md",
            findings_to_apply=[make_finding()],
        )

        result = await writer.get_instructions(StubRunContext(deps=deps))

        assert result.startswith("WRITER PROMPT")
        assert "# The Document" in result
        assert "Rewrite in active voice" in result

    def test_writer_has_no_context_toolset(self) -> None:
        from app.critique import tools

        assert tools.context_documents_toolset not in writer.writer_agent.toolsets
        assert tools.context_documents_toolset in critic.critic_agent.toolsets
