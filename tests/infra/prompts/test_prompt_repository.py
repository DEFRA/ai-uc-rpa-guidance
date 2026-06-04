import pytest

from app.infra.prompts import repository
from tests.infra.prompts import fakes


@pytest.fixture
def fake_fs() -> fakes.FakeFileSystem:
    """Fixture for creating a fake filesystem."""
    return fakes.FakeFileSystem()


class TestFileSystemPromptRepositoryLoad:
    @pytest.mark.asyncio
    async def test_load_prompt_file_returns_file_contents(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test that get_prompt_by_name returns the exact file contents."""

        prompt_name = "greeting.txt"
        prompt_content = "Hello, world!"

        fake_fs.set_file(prompt_name, prompt_content)

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        result = await repo.get_prompt_by_name(prompt_name)

        assert result == prompt_content

    @pytest.mark.asyncio
    async def test_load_prompt_with_multiline_content(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test loading a prompt with multiple lines."""

        prompt_name = "system.txt"
        prompt_content = (
            "You are a helpful assistant.\nAnswer questions concisely.\nBe friendly."
        )

        fake_fs.set_file(prompt_name, prompt_content)

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        result = await repo.get_prompt_by_name(prompt_name)

        assert result == prompt_content

    @pytest.mark.asyncio
    async def test_load_prompt_preserves_whitespace(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test that whitespace and formatting are preserved."""

        prompt_name = "format.txt"
        prompt_content = "  indented\n\ttabbed\n\n  double newline"

        fake_fs.set_file(prompt_name, prompt_content)

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        result = await repo.get_prompt_by_name(prompt_name)

        assert result == prompt_content


class TestFileSystemPromptRepositoryErrors:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_missing_prompt_raises_error(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test that requesting a non-existent prompt raises PromptNotFoundError."""

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        with pytest.raises(repository.PromptNotFoundError):
            await repo.get_prompt_by_name("nonexistent.txt")

    @pytest.mark.asyncio
    async def test_missing_prompt_error_message_includes_name(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test that the error message includes the missing prompt name."""
        prompt_name = "missing.txt"

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        with pytest.raises(repository.PromptNotFoundError) as exc_info:
            await repo.get_prompt_by_name(prompt_name)

        error_message = str(exc_info.value)
        assert prompt_name in error_message

    @pytest.mark.asyncio
    async def test_missing_prompt_error_message_includes_path(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test that the error message includes the full file path for debugging."""
        prompt_name = "missing.txt"
        expected_path = "/missing.txt"

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        with pytest.raises(repository.PromptNotFoundError) as exc_info:
            await repo.get_prompt_by_name(prompt_name)

        error_message = str(exc_info.value)
        assert expected_path in error_message
        assert "path:" in error_message


class TestFileSystemPromptRepositoryMultiplePrompts:
    """Test handling multiple prompts in the same directory."""

    @pytest.mark.asyncio
    async def test_load_multiple_different_prompts(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test loading different prompts from the same directory."""

        prompts = {
            "greeting.txt": "Hello!",
            "farewell.txt": "Goodbye!",
            "question.txt": "How are you?",
        }

        for name, content in prompts.items():
            fake_fs.set_file(name, content)

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        for name, expected_content in prompts.items():
            result = await repo.get_prompt_by_name(name)
            assert result == expected_content
