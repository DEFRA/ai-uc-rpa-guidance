"""Integration tests for MongoFeedbackRepository using TestContainers."""

import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime

import pymongo
import pymongo.asynchronous.database
import pytest
from testcontainers.mongodb import MongoDbContainer

from app.feedback import models, repository


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
    db = client["test_feedback"]
    yield db
    await client.drop_database("test_feedback")
    await client.close()


@pytest.fixture
def repo(
    mongo_db: pymongo.asynchronous.database.AsyncDatabase,
) -> repository.MongoFeedbackRepository:
    return repository.MongoFeedbackRepository(mongo_db)


def _make_snapshot(
    agent: models.AgentName = models.AgentName.CHECKER,
) -> models.FindingSnapshot:
    return models.FindingSnapshot(
        agent=agent,
        severity="high",
        fields={"issue": "Broken link", "category": "links"},
    )


def _make_entry(**overrides: object) -> models.FeedbackEntry:
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "agent": models.AgentName.CHECKER,
        "finding_index": 0,
        "verdict": models.FeedbackVerdict.FIX,
        "comment": None,
        "finding_snapshot": _make_snapshot(),
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return models.FeedbackEntry(**defaults)  # type: ignore[arg-type]


class TestCreate:
    async def test_persists_entry_and_returns_it(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        entry = _make_entry()

        result = await repo.create(entry)

        assert result.id == entry.id

    async def test_persists_all_fields(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        job_id = uuid.uuid4()
        entry = _make_entry(
            job_id=job_id,
            agent=models.AgentName.CRITIC,
            finding_index=2,
            verdict=models.FeedbackVerdict.FALSE_POSITIVE,
            comment="Expected behaviour",
        )

        await repo.create(entry)

        stored = await repo.collection.find_one({"_id": entry.id})
        assert stored is not None
        assert stored["job_id"] == job_id
        assert stored["agent"] == "critic"
        assert stored["finding_index"] == 2
        assert stored["verdict"] == "false_positive"
        assert stored["comment"] == "Expected behaviour"

    async def test_persists_none_finding_index_for_job_level(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        entry = _make_entry(finding_index=None)

        await repo.create(entry)

        stored = await repo.collection.find_one({"_id": entry.id})
        assert stored is not None
        assert stored["finding_index"] is None

    async def test_persists_finding_snapshot(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        snapshot = _make_snapshot(agent=models.AgentName.CHECKER)
        entry = _make_entry(finding_snapshot=snapshot)

        await repo.create(entry)

        stored = await repo.collection.find_one({"_id": entry.id})
        assert stored is not None
        assert stored["finding_snapshot"]["agent"] == "checker"
        assert stored["finding_snapshot"]["severity"] == "high"

    async def test_persists_none_snapshot(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        entry = _make_entry(finding_snapshot=None)

        await repo.create(entry)

        stored = await repo.collection.find_one({"_id": entry.id})
        assert stored is not None
        assert stored["finding_snapshot"] is None


class TestGetById:
    async def test_returns_entry_by_id(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        entry = _make_entry()
        await repo.create(entry)

        result = await repo.get_by_id(entry.id)

        assert result is not None
        assert result.id == entry.id
        assert result.verdict == models.FeedbackVerdict.FIX

    async def test_returns_none_when_not_found(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        result = await repo.get_by_id(uuid.uuid4())
        assert result is None

    async def test_round_trips_snapshot(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        snapshot = models.FindingSnapshot(
            agent=models.AgentName.CRITIC,
            severity="medium",
            fields={"what": "Passive voice", "where": "Introduction"},
        )
        entry = _make_entry(agent=models.AgentName.CRITIC, finding_snapshot=snapshot)
        await repo.create(entry)

        result = await repo.get_by_id(entry.id)

        assert result is not None
        assert result.finding_snapshot is not None
        assert result.finding_snapshot.agent == models.AgentName.CRITIC
        assert result.finding_snapshot.severity == "medium"
        assert result.finding_snapshot.fields["what"] == "Passive voice"


class TestGetForJob:
    async def test_returns_all_entries_for_job(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        job_id = uuid.uuid4()
        a = _make_entry(
            job_id=job_id, finding_index=0, created_at=datetime(2024, 1, 1, tzinfo=UTC)
        )
        b = _make_entry(
            job_id=job_id, finding_index=1, created_at=datetime(2024, 6, 1, tzinfo=UTC)
        )
        await repo.create(a)
        await repo.create(b)

        results = await repo.get_for_job(job_id)

        assert len(results) == 2

    async def test_returns_entries_ordered_by_created_at_ascending(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        job_id = uuid.uuid4()
        early = _make_entry(
            job_id=job_id, finding_index=0, created_at=datetime(2024, 1, 1, tzinfo=UTC)
        )
        late = _make_entry(
            job_id=job_id, finding_index=1, created_at=datetime(2024, 6, 1, tzinfo=UTC)
        )
        await repo.create(late)
        await repo.create(early)

        results = await repo.get_for_job(job_id)

        assert results[0].id == early.id
        assert results[1].id == late.id

    async def test_returns_empty_list_when_no_entries(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        results = await repo.get_for_job(uuid.uuid4())
        assert results == []

    async def test_does_not_return_entries_from_other_jobs(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        job_id = uuid.uuid4()
        other_job_id = uuid.uuid4()
        await repo.create(_make_entry(job_id=job_id, finding_index=0))
        await repo.create(_make_entry(job_id=other_job_id, finding_index=0))

        results = await repo.get_for_job(job_id)

        assert len(results) == 1
        assert results[0].job_id == job_id


class TestGetForFinding:
    async def test_returns_entry_for_finding(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        job_id = uuid.uuid4()
        entry = _make_entry(job_id=job_id, finding_index=3)
        await repo.create(entry)

        result = await repo.get_for_finding(job_id, 3)

        assert result is not None
        assert result.id == entry.id
        assert result.finding_index == 3

    async def test_returns_none_when_no_feedback_for_finding(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        result = await repo.get_for_finding(uuid.uuid4(), 0)
        assert result is None

    async def test_returns_job_level_entry_when_finding_index_is_none(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        job_id = uuid.uuid4()
        entry = _make_entry(job_id=job_id, finding_index=None)
        await repo.create(entry)

        result = await repo.get_for_finding(job_id, None)

        assert result is not None
        assert result.finding_index is None

    async def test_does_not_match_different_finding_index(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        job_id = uuid.uuid4()
        await repo.create(_make_entry(job_id=job_id, finding_index=0))

        result = await repo.get_for_finding(job_id, 1)

        assert result is None


class TestUpdate:
    async def test_updates_verdict(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        entry = _make_entry(verdict=models.FeedbackVerdict.FIX)
        await repo.create(entry)

        result = await repo.update(entry.id, models.FeedbackVerdict.WONT_FIX, None)

        assert result is not None
        assert result.verdict == models.FeedbackVerdict.WONT_FIX

    async def test_updates_comment(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        entry = _make_entry(comment=None)
        await repo.create(entry)

        result = await repo.update(entry.id, None, "Updated comment")

        assert result is not None
        assert result.comment == "Updated comment"

    async def test_updates_both_verdict_and_comment(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        entry = _make_entry(verdict=models.FeedbackVerdict.FIX, comment=None)
        await repo.create(entry)

        result = await repo.update(
            entry.id, models.FeedbackVerdict.FALSE_POSITIVE, "Not real"
        )

        assert result is not None
        assert result.verdict == models.FeedbackVerdict.FALSE_POSITIVE
        assert result.comment == "Not real"

    async def test_leaves_unchanged_fields_intact(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        entry = _make_entry(verdict=models.FeedbackVerdict.FIX, comment="Original")
        await repo.create(entry)

        result = await repo.update(entry.id, None, None)

        assert result is not None
        assert result.verdict == models.FeedbackVerdict.FIX
        assert result.finding_snapshot is not None

    async def test_returns_none_when_entry_not_found(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        result = await repo.update(uuid.uuid4(), models.FeedbackVerdict.FIX, None)
        assert result is None

    async def test_updates_updated_at_timestamp(
        self, repo: repository.MongoFeedbackRepository
    ) -> None:
        original_time = datetime(2024, 1, 1, tzinfo=UTC)
        entry = _make_entry(updated_at=original_time)
        await repo.create(entry)

        result = await repo.update(entry.id, models.FeedbackVerdict.WONT_FIX, None)

        assert result is not None
        # MongoDB returns naive datetimes; strip tzinfo before comparing
        assert result.updated_at > original_time.replace(tzinfo=None)
