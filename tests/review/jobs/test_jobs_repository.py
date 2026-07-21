"""Integration tests for MongoReviewJobRepository using TestContainers."""

import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime

import pymongo
import pymongo.asynchronous.database
import pytest
from testcontainers.mongodb import MongoDbContainer

from app.review.jobs import models, repository


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
    db = client["test_review_jobs"]
    yield db
    await client.drop_database("test_review_jobs")
    await client.close()


@pytest.fixture
def repo(
    mongo_db: pymongo.asynchronous.database.AsyncDatabase,
) -> repository.MongoReviewJobRepository:
    return repository.MongoReviewJobRepository(mongo_db)


def _make_job(**overrides: object) -> models.ReviewJob:
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "document_id": uuid.uuid4(),
        "status": models.JobStatus.PENDING,
    }
    defaults.update(overrides)
    return models.ReviewJob(**defaults)  # type: ignore[arg-type]


class TestCreateJob:
    async def test_creates_job_with_provided_id(
        self, repo: repository.MongoReviewJobRepository
    ) -> None:
        job = _make_job()
        result = await repo.create_job(job)
        assert result.id == job.id

    async def test_persists_all_fields(
        self, repo: repository.MongoReviewJobRepository
    ) -> None:
        doc_id = uuid.uuid4()
        job = _make_job(document_id=doc_id, status=models.JobStatus.PENDING)

        await repo.create_job(job)

        stored = await repo.collection.find_one({"_id": job.id})
        assert stored is not None
        assert stored["document_id"] == doc_id
        assert stored["status"] == "pending"
        assert stored["result"] is None
        assert stored["error_message"] is None


class TestGetJob:
    async def test_returns_job_by_id(
        self, repo: repository.MongoReviewJobRepository
    ) -> None:
        job = _make_job()
        await repo.create_job(job)

        result = await repo.get_job(job.id)

        assert result is not None
        assert result.id == job.id
        assert result.status == models.JobStatus.PENDING

    async def test_returns_none_when_not_found(
        self, repo: repository.MongoReviewJobRepository
    ) -> None:
        result = await repo.get_job(uuid.uuid4())
        assert result is None


class TestGetLatestForDocument:
    async def test_returns_most_recent_job(
        self, repo: repository.MongoReviewJobRepository
    ) -> None:
        doc_id = uuid.uuid4()
        early = _make_job(
            document_id=doc_id,
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        late = _make_job(
            document_id=doc_id,
            created_at=datetime(2024, 6, 1, tzinfo=UTC),
        )
        await repo.create_job(early)
        await repo.create_job(late)

        result = await repo.get_latest_for_document(doc_id)

        assert result is not None
        assert result.id == late.id

    async def test_returns_none_when_no_jobs_exist(
        self, repo: repository.MongoReviewJobRepository
    ) -> None:
        result = await repo.get_latest_for_document(uuid.uuid4())
        assert result is None


class TestUpdateStatus:
    async def test_updates_status(
        self, repo: repository.MongoReviewJobRepository
    ) -> None:
        job = _make_job()
        await repo.create_job(job)

        await repo.update_status(job.id, models.JobStatus.RUNNING)

        result = await repo.get_job(job.id)
        assert result is not None
        assert result.status == models.JobStatus.RUNNING


class TestStoreResult:
    async def test_stores_result_and_marks_completed(
        self, repo: repository.MongoReviewJobRepository
    ) -> None:
        job = _make_job()
        await repo.create_job(job)

        await repo.store_result(
            job.id, {"status": "completed", "usability": {"verdict": "partly"}}
        )

        result = await repo.get_job(job.id)
        assert result is not None
        assert result.status == models.JobStatus.COMPLETED
        assert result.result == {
            "status": "completed",
            "usability": {"verdict": "partly"},
        }


class TestSetError:
    async def test_sets_error_status_and_message(
        self, repo: repository.MongoReviewJobRepository
    ) -> None:
        job = _make_job()
        await repo.create_job(job)

        await repo.set_error(job.id, "Bedrock timeout")

        result = await repo.get_job(job.id)
        assert result is not None
        assert result.status == models.JobStatus.ERROR
        assert result.error_message == "Bedrock timeout"
