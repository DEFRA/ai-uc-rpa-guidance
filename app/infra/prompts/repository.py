"""Prompt repository for managing QA analysis system prompts."""

import os
from abc import ABC, abstractmethod

import aiofiles


class PromptNotFoundError(Exception):
    """Raised when a prompt file cannot be found."""


class FileSystem:
    """File system operations wrapper."""

    def directory_exists(self, path: str) -> bool:
        """Check if a directory exists."""
        return os.path.isdir(path)

    async def read_file(self, path: str) -> str:
        """Asynchronously read a file's contents."""
        async with aiofiles.open(path) as f:
            return str(await f.read()).strip()


class AbstractPromptRepository(ABC):
    """Abstract base class for prompt repositories."""

    @abstractmethod
    async def get_prompt_by_name(self, name: str) -> str:
        """Retrieve a prompt by name.

        Args:
            name: The name of the prompt file (e.g., "publishing_agent.md").

        Returns:
            The content of the prompt file.

        Raises:
            PromptNotFoundError: If the prompt file cannot be found.
        """


class FileSystemPromptRepository(AbstractPromptRepository):
    """File system based prompt repository with in-memory caching."""

    def __init__(self, prompt_directory: str, fs: FileSystem = FileSystem()) -> None:
        """Initialize the repository.

        Args:
            prompt_directory: The directory where prompt files are located.
            fs: Optional FileSystem instance for testing. Defaults to FileSystem().
        """
        self.fs = fs or FileSystem()
        self.prompt_directory = prompt_directory

    async def get_prompt_by_name(self, name: str) -> str:
        """Retrieve a prompt by name with caching.

        Args:
            name: The name of the prompt file.

        Returns:
            The content of the prompt file.

        Raises:
            PromptNotFoundError: If the prompt file cannot be found.
        """

        full_path = os.path.join(self.prompt_directory, name)

        try:
            content = await self.fs.read_file(full_path)
        except FileNotFoundError as err:
            msg = f"Prompt not found: {name}"
            raise PromptNotFoundError(msg) from err

        return content
