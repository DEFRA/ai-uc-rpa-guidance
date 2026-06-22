"""Fakes for critique module tests."""

from app.infra.context import repository as context_repo


class FakeContextRepository(context_repo.AbstractContextRepository):
    """In-memory fake context repository."""

    def __init__(self, documents: dict[str, str] | None = None) -> None:
        self.documents = documents or {}
        self.requested_keys: list[str] = []

    async def get_context(self, key: str) -> str:
        self.requested_keys.append(key)
        if key not in self.documents:
            msg = f"Context document not found: {key}"
            raise context_repo.ContextNotFoundError(msg)
        return self.documents[key]
