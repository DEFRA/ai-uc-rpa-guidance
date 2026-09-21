"""Integration tests for SummaryRepository using TestContainers."""

import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime

import pymongo
import pymongo.asynchronous.database
import pytest
from testcontainers.mongodb import MongoDbContainer

from app.guidance.summaries import models, repository


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
    db = client["test_summaries"]
    yield db
    await client.drop_database("test_summaries")
    await client.close()


@pytest.fixture
def repo(
    mongo_db: pymongo.asynchronous.database.AsyncDatabase,
) -> repository.SummaryRepository:
    return repository.SummaryRepository(mongo_db)


def _make_summary(**overrides: object) -> models.DocumentSummary:
    defaults: dict[str, object] = {
        "document_id": uuid.uuid4(),
        "title": "A Guide",
        "about": "What it is about.",
        "used_for": "What it is used for.",
        "keywords": ["ROCR"],
        "acronyms": [
            models.AcronymEntry(
                acronym="SDA",
                expansion="Severely Disadvantaged Area",
                sections=["1", "2"],
            )
        ],
        "path": "s3://bucket/parsed_guidance/x/summary.md",
        "start_path": "/guidance-documents/x/sections/1",
        "model": "anthropic.claude-sonnet-4-6",
    }
    defaults.update(overrides)
    return models.DocumentSummary(**defaults)  # type: ignore[arg-type]


class TestSaveSummary:
    async def test_persists_all_fields(
        self, repo: repository.SummaryRepository
    ) -> None:
        summary = _make_summary()

        await repo.save_summary(summary)

        stored = await repo.get_summary(summary.document_id)
        assert stored is not None
        assert stored.title == summary.title
        assert stored.about == summary.about
        assert stored.used_for == summary.used_for
        assert stored.keywords == summary.keywords
        assert stored.acronyms == summary.acronyms
        assert stored.path == summary.path
        assert stored.start_path == summary.start_path
        assert stored.model == summary.model

    async def test_a_document_holds_one_summary(
        self, repo: repository.SummaryRepository
    ) -> None:
        document_id = uuid.uuid4()
        await repo.save_summary(_make_summary(document_id=document_id, about="First"))

        await repo.save_summary(_make_summary(document_id=document_id, about="Second"))

        stored = await repo.get_summary(document_id)
        assert stored is not None
        assert stored.about == "Second"
        assert await repo.collection.count_documents({"_id": document_id}) == 1

    async def test_rebuilding_keeps_when_the_summary_first_appeared(
        self, repo: repository.SummaryRepository
    ) -> None:
        document_id = uuid.uuid4()
        first = datetime(2026, 1, 1, tzinfo=UTC)
        await repo.save_summary(
            _make_summary(document_id=document_id, created_at=first, updated_at=first)
        )

        later = datetime(2026, 6, 1, tzinfo=UTC)
        await repo.save_summary(
            _make_summary(document_id=document_id, created_at=later, updated_at=later)
        )

        stored = await repo.get_summary(document_id)
        assert stored is not None
        # pymongo reads BSON dates back without a timezone; the instant is
        # what this asserts, not how it is tagged.
        assert stored.created_at.replace(tzinfo=UTC) == first
        assert stored.updated_at.replace(tzinfo=UTC) == later


class TestGetSummary:
    async def test_returns_none_for_an_unsummarised_document(
        self, repo: repository.SummaryRepository
    ) -> None:
        assert await repo.get_summary(uuid.uuid4()) is None


class TestGetSummaryWithoutALink:
    async def test_opens_the_contents_page_for_a_record_written_before_it(
        self, repo: repository.SummaryRepository
    ) -> None:
        summary = _make_summary()
        await repo.save_summary(summary)
        await repo.collection.update_one(
            {"_id": summary.document_id}, {"$unset": {"start_path": ""}}
        )

        stored = await repo.get_summary(summary.document_id)

        assert stored is not None
        assert stored.start_path == f"/guidance-documents/{summary.document_id}/view"


class TestDeleteAllSummaries:
    async def test_discards_every_summary(
        self, repo: repository.SummaryRepository
    ) -> None:
        await repo.save_summary(_make_summary())
        await repo.save_summary(_make_summary())

        deleted = await repo.delete_all_summaries()

        assert deleted == 2
        assert await repo.list_summaries() == []

    async def test_discards_nothing_from_an_empty_index(
        self, repo: repository.SummaryRepository
    ) -> None:
        assert await repo.delete_all_summaries() == 0


class TestListSummaries:
    async def test_lists_most_recently_rebuilt_first(
        self, repo: repository.SummaryRepository
    ) -> None:
        older = _make_summary(
            title="Older", updated_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
        newer = _make_summary(
            title="Newer", updated_at=datetime(2026, 6, 1, tzinfo=UTC)
        )
        await repo.save_summary(older)
        await repo.save_summary(newer)

        summaries = await repo.list_summaries()

        assert [summary.title for summary in summaries] == ["Newer", "Older"]

    async def test_is_empty_when_nothing_is_summarised(
        self, repo: repository.SummaryRepository
    ) -> None:
        assert await repo.list_summaries() == []


@pytest.fixture
def section_repo(
    mongo_db: pymongo.asynchronous.database.AsyncDatabase,
) -> repository.SectionSummaryRepository:
    return repository.SectionSummaryRepository(mongo_db)


def _make_section(
    document_id: uuid.UUID, number: str, order: int = 0, summary: str = "About it."
) -> models.SectionSummary:
    return models.SectionSummary(
        document_id=document_id,
        number=number,
        heading=f"Heading {number}",
        level=1,
        summary=summary,
        keywords=["a term", "SDA"],
        acronyms=[
            models.SectionAcronym(
                acronym="SDA", expansion="Severely Disadvantaged Area"
            )
        ],
        start_path=f"/guidance-documents/{document_id}/sections/{number}",
        order=order,
    )


class TestSaveSections:
    async def test_persists_all_fields(
        self, section_repo: repository.SectionSummaryRepository
    ) -> None:
        document_id = uuid.uuid4()

        await section_repo.save_sections([_make_section(document_id, "1")])

        stored = await section_repo.list_sections()
        assert len(stored) == 1
        assert stored[0].document_id == document_id
        assert stored[0].number == "1"
        assert stored[0].heading == "Heading 1"
        assert stored[0].keywords == ["a term", "SDA"]
        assert stored[0].acronyms == [
            models.SectionAcronym(
                acronym="SDA", expansion="Severely Disadvantaged Area"
            )
        ]
        assert stored[0].start_path.endswith("/sections/1")

    async def test_a_section_holds_one_entry(
        self, section_repo: repository.SectionSummaryRepository
    ) -> None:
        document_id = uuid.uuid4()
        await section_repo.save_sections(
            [_make_section(document_id, "1", summary="First")]
        )

        await section_repo.save_sections(
            [_make_section(document_id, "1", summary="Second")]
        )

        stored = await section_repo.list_sections()
        assert len(stored) == 1
        assert stored[0].summary == "Second"

    async def test_the_same_number_in_two_documents_is_two_entries(
        self, section_repo: repository.SectionSummaryRepository
    ) -> None:
        await section_repo.save_sections([_make_section(uuid.uuid4(), "1")])
        await section_repo.save_sections([_make_section(uuid.uuid4(), "1")])

        assert len(await section_repo.list_sections()) == 2

    async def test_saves_nothing_for_a_document_with_no_sections(
        self, section_repo: repository.SectionSummaryRepository
    ) -> None:
        await section_repo.save_sections([])

        assert await section_repo.list_sections() == []


class TestListSections:
    async def test_lists_in_document_order(
        self, section_repo: repository.SectionSummaryRepository
    ) -> None:
        document_id = uuid.uuid4()
        await section_repo.save_sections(
            [
                _make_section(document_id, "1.10", order=2),
                _make_section(document_id, "1", order=0),
                _make_section(document_id, "1.9", order=1),
            ]
        )

        stored = await section_repo.list_sections()

        assert [entry.number for entry in stored] == ["1", "1.9", "1.10"]


class TestDeleteAllSections:
    async def test_discards_every_entry(
        self, section_repo: repository.SectionSummaryRepository
    ) -> None:
        await section_repo.save_sections(
            [_make_section(uuid.uuid4(), "1"), _make_section(uuid.uuid4(), "2")]
        )

        deleted = await section_repo.delete_all_sections()

        assert deleted == 2
        assert await section_repo.list_sections() == []
