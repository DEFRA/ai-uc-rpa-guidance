"""Tests for the guidance search API endpoints."""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import fastapi.testclient
import pytest

import app.entrypoints.fastapi
from app.guidance.search import api_schemas, dependencies


@pytest.fixture
def search_service() -> AsyncMock:
    """A mock search service."""
    return AsyncMock()


@pytest.fixture
def client(search_service: AsyncMock) -> Iterator[fastapi.testclient.TestClient]:
    """A test client whose search service is the mock."""
    test_app = app.entrypoints.fastapi.app
    test_app.dependency_overrides[dependencies.get_search_service] = lambda: (
        search_service
    )
    yield fastapi.testclient.TestClient(test_app)
    test_app.dependency_overrides.pop(dependencies.get_search_service)


class TestKind:
    def test_says_whether_a_search_is_natural_language(
        self, client: fastapi.testclient.TestClient, search_service: AsyncMock
    ) -> None:
        search_service.kind.return_value = api_schemas.QueryKindResponse(
            natural_language=True
        )

        response = client.get("/guidance/search/kind", params={"q": "NA02 meaning"})

        assert response.status_code == 200
        assert response.json() == {"naturalLanguage": True}
        search_service.kind.assert_awaited_once_with("NA02 meaning")

    def test_refuses_an_empty_search(
        self, client: fastapi.testclient.TestClient
    ) -> None:
        response = client.get("/guidance/search/kind", params={"q": ""})

        assert response.status_code == 422
