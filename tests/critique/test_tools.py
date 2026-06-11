"""Tests for the context document tools."""

import dataclasses

import pytest

from app.critique import tools
from tests.critique import fakes


@dataclasses.dataclass
class StubRunContext:
    """Minimal stand-in for pydantic_ai.RunContext carrying deps."""

    deps: object


@dataclasses.dataclass
class StubDeps:
    context_repository: fakes.FakeContextRepository


@pytest.fixture
def context_repository() -> fakes.FakeContextRepository:
    return fakes.FakeContextRepository(
        {
            "content-style-guide/index.json": '[{"title": "Dates"}]',
            "content-guidance/index.json": '[{"title": "Writing for GOV.UK"}]',
            "defra-style-guide/index.json": '[{"title": "Defra style guide — A"}]',
            "content-style-guide/dates.md": "Use 5 July 2026.",
        }
    )


@pytest.fixture
def ctx(context_repository: fakes.FakeContextRepository) -> StubRunContext:
    return StubRunContext(deps=StubDeps(context_repository=context_repository))


class TestListTools:
    async def test_list_style_guide_documents_returns_index(
        self, ctx: StubRunContext
    ) -> None:
        result = await tools.list_style_guide_documents(ctx)

        assert result == '[{"title": "Dates"}]'

    async def test_list_content_guidance_returns_index(
        self, ctx: StubRunContext
    ) -> None:
        result = await tools.list_content_guidance(ctx)

        assert result == '[{"title": "Writing for GOV.UK"}]'

    async def test_list_defra_style_guide_documents_returns_index(
        self, ctx: StubRunContext
    ) -> None:
        result = await tools.list_defra_style_guide_documents(ctx)

        assert result == '[{"title": "Defra style guide — A"}]'

    async def test_missing_index_returns_error_string(self) -> None:
        empty_ctx = StubRunContext(
            deps=StubDeps(context_repository=fakes.FakeContextRepository())
        )

        result = await tools.list_style_guide_documents(empty_ctx)

        assert result.startswith("Error retrieving index")
        assert "content-style-guide/index.json" in result


class TestGetDocumentContent:
    async def test_returns_document_content(self, ctx: StubRunContext) -> None:
        result = await tools.get_document_content(ctx, "content-style-guide/dates.md")

        assert result == "Use 5 July 2026."

    async def test_missing_document_returns_error_string(
        self, ctx: StubRunContext
    ) -> None:
        result = await tools.get_document_content(ctx, "missing.md")

        assert result.startswith("Error retrieving document content")
        assert "missing.md" in result


class TestToolsetRegistration:
    def test_all_tools_registered(self) -> None:
        registered = set(tools.context_documents_toolset.tools.keys())

        assert registered == {
            "list_style_guide_documents",
            "list_content_guidance",
            "list_defra_style_guide_documents",
            "get_document_content",
        }
