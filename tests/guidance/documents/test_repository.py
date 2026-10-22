"""Integration tests for GuidanceRepository using TestContainers."""

import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime

import pymongo
import pymongo.asynchronous.database
import pytest
from testcontainers.community.mongodb import MongoDbContainer

from app.guidance.documents import models, repository


@pytest.fixture(scope="session")
def mongodb_container() -> Generator[MongoDbContainer]:
    with MongoDbContainer("mongo:7") as container:
        yield container


@pytest.fixture
async def mongo_db(
    mongodb_container: MongoDbContainer,
) -> AsyncGenerator[pymongo.asynchronous.database.AsyncDatabase]:
    client = pymongo.AsyncMongoClient(
        mongodb_container.get_connection_url(), uuidRepresentation="standard"
    )
    db = client["test_guidance"]
    yield db
    await client.drop_database("test_guidance")
    await client.close()


@pytest.fixture
def repo(
    mongo_db: pymongo.asynchronous.database.AsyncDatabase,
) -> repository.GuidanceRepository:
    return repository.GuidanceRepository(mongo_db)


def _make_document(**overrides: object) -> models.GuidanceDocument:
    defaults = {
        "id": uuid.uuid4(),
        "title": "Test Document",
        "description": "A test document",
        "filename": "test.docx",
        "path": "documents/test.docx",
        "status": models.ExtractionStatus.PENDING,
        "content_hash": "abc123",
    }
    defaults.update(overrides)
    return models.GuidanceDocument(**defaults)  # type: ignore[arg-type]


class TestCreateDocument:
    async def test_creates_document_with_provided_id(
        self, repo: repository.GuidanceRepository
    ) -> None:
        document = _make_document()

        result = await repo.create_document(document)

        assert result.id == document.id

    async def test_persists_all_fields(
        self, repo: repository.GuidanceRepository
    ) -> None:
        document = _make_document(
            title="Persist Test",
            description="Check all fields",
            filename="persist.docx",
            path="docs/persist.docx",
            content_hash="hash456",
        )

        result = await repo.create_document(document)

        stored = await repo.collection.find_one({"_id": result.id})
        assert stored is not None
        assert stored["title"] == "Persist Test"
        assert stored["description"] == "Check all fields"
        assert stored["filename"] == "persist.docx"
        assert stored["path"] == "docs/persist.docx"
        assert stored["status"] == "pending"
        assert stored["content_hash"] == "hash456"


class TestGetDocument:
    async def test_returns_document_by_id(
        self, repo: repository.GuidanceRepository
    ) -> None:
        document = _make_document(title="Findable")
        created = await repo.create_document(document)

        result = await repo.get_document(created.id)

        assert result is not None
        assert result.id == created.id
        assert result.title == "Findable"

    async def test_returns_none_when_not_found(
        self, repo: repository.GuidanceRepository
    ) -> None:
        fake_id = uuid.uuid4()

        result = await repo.get_document(fake_id)

        assert result is None


class TestUpdateDocument:
    async def test_updates_fields(self, repo: repository.GuidanceRepository) -> None:
        document = _make_document(filename="original.docx")
        created = await repo.create_document(document)

        created.filename = "updated.docx"
        created.status = models.ExtractionStatus.COMPLETE
        await repo.update_document(created)

        fetched = await repo.get_document(created.id)
        assert fetched is not None
        assert fetched.filename == "updated.docx"
        assert fetched.status == models.ExtractionStatus.COMPLETE

    async def test_keeps_the_documents_it_links_to(
        self, repo: repository.GuidanceRepository
    ) -> None:
        created = await repo.create_document(_make_document())

        created.linked_filenames = ["Other Guide.docx"]
        await repo.update_document(created)

        fetched = await repo.get_document(created.id)
        assert fetched is not None
        assert fetched.linked_filenames == ["Other Guide.docx"]

    async def test_sets_updated_at(self, repo: repository.GuidanceRepository) -> None:
        document = _make_document()
        created = await repo.create_document(document)
        original_updated_at = created.updated_at

        created.filename = "changed.docx"
        result = await repo.update_document(created)

        assert result.updated_at >= original_updated_at


class TestGetDocumentWithoutLinks:
    async def test_reads_a_document_stored_before_links_were_kept_as_linking_to_none(
        self,
        repo: repository.GuidanceRepository,
        mongo_db: pymongo.asynchronous.database.AsyncDatabase,
    ) -> None:
        created = await repo.create_document(_make_document())
        await mongo_db[repository.COLLECTION_NAME].update_one(
            {"_id": created.id}, {"$unset": {"linked_filenames": ""}}
        )

        fetched = await repo.get_document(created.id)

        assert fetched is not None
        assert fetched.linked_filenames == []


class TestListOtherDocuments:
    async def test_returns_every_document_but_the_one_given(
        self, repo: repository.GuidanceRepository
    ) -> None:
        this = await repo.create_document(_make_document(filename="this.docx"))
        await repo.create_document(_make_document(filename="first.docx"))
        await repo.create_document(_make_document(filename="second.docx"))

        others = await repo.list_other_documents(this.id)

        assert sorted(d.filename for d in others) == ["first.docx", "second.docx"]


class TestListDocuments:
    async def test_returns_paginated_results(
        self, repo: repository.GuidanceRepository
    ) -> None:
        for i in range(5):
            await repo.create_document(_make_document(title=f"Doc {i}"))

        documents, _ = await repo.list_documents(page=1, page_size=3)

        assert len(documents) == 3

    async def test_returns_total_count(
        self, repo: repository.GuidanceRepository
    ) -> None:
        for i in range(4):
            await repo.create_document(_make_document(title=f"Doc {i}"))

        _, total = await repo.list_documents(page=1, page_size=2)

        assert total == 4

    async def test_sorts_by_created_at_descending(
        self, repo: repository.GuidanceRepository
    ) -> None:
        doc_early = _make_document(
            title="Early",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        doc_late = _make_document(
            title="Late",
            created_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        await repo.create_document(doc_early)
        await repo.create_document(doc_late)

        documents, _ = await repo.list_documents(page=1, page_size=10)

        assert documents[0].title == "Late"
        assert documents[1].title == "Early"
