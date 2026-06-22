"""Tests for the filesystem context repository."""

import pathlib

import pytest

from app.infra.context import repository


@pytest.fixture
def context_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A populated temporary context directory."""
    style_dir = tmp_path / "content-style-guide"
    style_dir.mkdir()
    (style_dir / "index.json").write_text('[{"title": "Dates", "file": "dates.md"}]')
    (style_dir / "dates.md").write_text("Use the format 5 July 2026.")
    return tmp_path


class TestFileSystemContextRepositoryLoad:
    async def test_returns_file_contents(self, context_dir: pathlib.Path) -> None:
        repo = repository.FileSystemContextRepository(str(context_dir))

        result = await repo.get_context("content-style-guide/dates.md")

        assert result == "Use the format 5 July 2026."

    async def test_returns_index_json(self, context_dir: pathlib.Path) -> None:
        repo = repository.FileSystemContextRepository(str(context_dir))

        result = await repo.get_context("content-style-guide/index.json")

        assert '"title": "Dates"' in result


class TestFileSystemContextRepositoryErrors:
    async def test_missing_document_raises_not_found(
        self, context_dir: pathlib.Path
    ) -> None:
        repo = repository.FileSystemContextRepository(str(context_dir))

        with pytest.raises(repository.ContextNotFoundError):
            await repo.get_context("content-style-guide/missing.md")

    async def test_error_message_includes_key(self, context_dir: pathlib.Path) -> None:
        repo = repository.FileSystemContextRepository(str(context_dir))

        with pytest.raises(repository.ContextNotFoundError) as exc_info:
            await repo.get_context("nope/missing.md")

        assert "nope/missing.md" in str(exc_info.value)

    async def test_not_found_is_a_repository_error(
        self, context_dir: pathlib.Path
    ) -> None:
        repo = repository.FileSystemContextRepository(str(context_dir))

        with pytest.raises(repository.ContextRepositoryError):
            await repo.get_context("missing.md")


class TestFileSystemContextRepositoryPathTraversal:
    """Keys come from LLM tool calls, so traversal must be rejected."""

    async def test_parent_traversal_rejected(
        self, context_dir: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        secret = tmp_path.parent / "secret.txt"
        secret.write_text("secret")
        repo = repository.FileSystemContextRepository(str(context_dir))

        with pytest.raises(repository.ContextNotFoundError):
            await repo.get_context("../secret.txt")

    async def test_absolute_path_rejected(self, context_dir: pathlib.Path) -> None:
        repo = repository.FileSystemContextRepository(str(context_dir))

        with pytest.raises(repository.ContextNotFoundError):
            await repo.get_context("/etc/hostname")

    async def test_nested_traversal_rejected(self, context_dir: pathlib.Path) -> None:
        repo = repository.FileSystemContextRepository(str(context_dir))

        with pytest.raises(repository.ContextNotFoundError):
            await repo.get_context("content-style-guide/../../escape.md")
