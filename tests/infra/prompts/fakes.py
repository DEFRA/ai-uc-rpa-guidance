import os


class FakeFileSystem:
    """In-memory fake filesystem for testing FileSystemPromptRepository."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.read_counts: dict[str, int] = {}

    def set_file(self, path: str, content: str) -> None:
        name = os.path.basename(path)
        self.files[name] = content

    def directory_exists(self, _: str) -> bool:
        """Check if a directory exists (mock: always True)."""
        return True

    async def read_file(self, path: str) -> str:
        name = os.path.basename(path)
        if name not in self.files:
            msg = f"File not found: {path}"
            raise FileNotFoundError(msg)

        self.read_counts[name] = self.read_counts.get(name, 0) + 1

        return self.files[name]

    def get_read_count(self, path: str) -> int:
        name = os.path.basename(path)
        return self.read_counts.get(name, 0)
