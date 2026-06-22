"""Context repository for serving reference documents (style guides, guidance).

Ported from DEFRA/ai-uc-content-swarm-runtime (S3-backed); this implementation
is filesystem-backed for the POC, mirroring FileSystemPromptRepository.
"""

import os
from abc import ABC, abstractmethod

from app.infra.prompts import repository as prompts_repository


class ContextRepositoryError(Exception):
    """Generic context repository error."""


class ContextNotFoundError(ContextRepositoryError):
    """Raised when a context document is not found."""


class AbstractContextRepository(ABC):
    """Abstract repository for loading context documents."""

    @abstractmethod
    async def get_context(self, key: str) -> str:
        """Retrieve a single context document by key.

        Args:
            key: The document key, e.g. "content-style-guide/index.json".

        Returns:
            The content of the context document.

        Raises:
            ContextNotFoundError: If the document is not found.
            ContextRepositoryError: If retrieval fails.
        """


class FileSystemContextRepository(AbstractContextRepository):
    """Filesystem-backed repository for context documents."""

    def __init__(
        self,
        context_directory: str,
        fs: prompts_repository.FileSystem = prompts_repository.FileSystem(),
    ) -> None:
        """Initialize the repository.

        Args:
            context_directory: Root directory containing context documents.
            fs: Optional FileSystem instance for testing.
        """
        self.context_directory = context_directory
        self.fs = fs

    def _resolve(self, key: str) -> str:
        """Resolve a key to a path, rejecting traversal outside the root.

        Keys come from LLM tool calls, so they are untrusted input.
        """
        root = os.path.abspath(self.context_directory)
        full_path = os.path.abspath(os.path.normpath(os.path.join(root, key)))

        if full_path != root and not full_path.startswith(root + os.sep):
            msg = f"Context document not found: {key}"
            raise ContextNotFoundError(msg)

        return full_path

    async def get_context(self, key: str) -> str:
        """Retrieve a context document from the filesystem.

        Args:
            key: Path of the document relative to the context directory.

        Returns:
            The content of the context document.

        Raises:
            ContextNotFoundError: If the document is not found.
            ContextRepositoryError: If retrieval fails.
        """
        full_path = self._resolve(key)

        try:
            return await self.fs.read_file(full_path)
        except FileNotFoundError as err:
            msg = f"Context document not found: {key}"
            raise ContextNotFoundError(msg) from err
        except ContextRepositoryError:
            raise
        except Exception as err:
            msg = f"Failed to read context document '{key}': {err}"
            raise ContextRepositoryError(msg) from err
