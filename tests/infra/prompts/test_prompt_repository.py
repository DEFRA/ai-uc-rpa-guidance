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


class TestFileSystemPromptRepositoryCaching:
    @pytest.mark.asyncio
    async def test_caching_prevents_re_read(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test that subsequent calls use cache and don't re-read file."""

        prompt_name = "cached.txt"
        prompt_content = "Cached content"

        fake_fs.set_file(prompt_name, prompt_content)

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        result1 = await repo.get_prompt_by_name(prompt_name)
        result2 = await repo.get_prompt_by_name(prompt_name)

        assert fake_fs.get_read_count(prompt_name) == 1
        assert result1 == prompt_content
        assert result2 == prompt_content

    @pytest.mark.asyncio
    async def test_cache_is_persistent_across_calls(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test that cache persists across multiple calls."""

        prompt_name = "persistent.txt"
        prompt_content = "This should be cached"

        fake_fs.set_file(prompt_name, prompt_content)

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        await repo.get_prompt_by_name(prompt_name)
        await repo.get_prompt_by_name(prompt_name)

        result = await repo.get_prompt_by_name(prompt_name)

        assert repo._cache[prompt_name] == prompt_content
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

        assert prompt_name in str(exc_info.value)


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

    @pytest.mark.asyncio
    async def test_cache_stores_different_prompts(
        self, fake_fs: fakes.FakeFileSystem
    ) -> None:
        """Test that cache correctly stores multiple different prompts."""
        prompts = {
            "prompt1.txt": "Content 1",
            "prompt2.txt": "Content 2",
        }

        for name, content in prompts.items():
            fake_fs.set_file(name, content)

        repo = repository.FileSystemPromptRepository(prompt_directory="/", fs=fake_fs)

        for name in prompts:
            await repo.get_prompt_by_name(name)

        assert len(repo._cache) == 2
        assert repo._cache["prompt1.txt"] == "Content 1"
        assert repo._cache["prompt2.txt"] == "Content 2"
