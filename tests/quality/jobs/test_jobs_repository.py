"""Integration tests for MongoQualityJobRepository using TestContainers."""

import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta

import pymongo
import pymongo.asynchronous.database
import pytest
from testcontainers.community.mongodb import MongoDbContainer

from app.quality.jobs import models, repository

DATABASE = "test_quality_jobs"


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
    yield client[DATABASE]
    await client.drop_database(DATABASE)
    await client.close()


@pytest.fixture
def repo(
    mongo_db: pymongo.asynchronous.database.AsyncDatabase,
) -> repository.MongoQualityJobRepository:
    return repository.MongoQualityJobRepository(mongo_db)


def _make_job(
    document_id: uuid.UUID | None = None, created_at: datetime | None = None
) -> models.QualityJob:
    now = created_at or datetime.now(tz=UTC)
    return models.QualityJob(
        id=uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        version_id="ad1680a5-b12f-4b1f-9b7d-6ec1539c6fdf",
        created_at=now,
        updated_at=now,
    )


class TestMongoQualityJobRepository:
    async def test_a_created_job_can_be_read_back(
        self, repo: repository.MongoQualityJobRepository
    ) -> None:
        job = _make_job()

        await repo.create_job(job)
        found = await repo.get_job(job.id)

        assert found is not None
        assert found.document_id == job.document_id
        assert found.version_id == job.version_id
        assert found.status == models.JobStatus.PENDING

    async def test_an_unknown_job_is_not_found(
        self, repo: repository.MongoQualityJobRepository
    ) -> None:
        assert await repo.get_job(uuid.uuid4()) is None

    async def test_every_run_is_kept_and_the_latest_is_returned(
        self, repo: repository.MongoQualityJobRepository
    ) -> None:
        document_id = uuid.uuid4()
        earlier = _make_job(document_id, datetime.now(tz=UTC) - timedelta(hours=1))
        later = _make_job(document_id)
        await repo.create_job(earlier)
        await repo.create_job(later)

        latest = await repo.get_latest_for_document(document_id)

        assert latest is not None
        assert latest.id == later.id
        assert await repo.get_job(earlier.id) is not None

    async def test_no_runs_means_no_latest(
        self, repo: repository.MongoQualityJobRepository
    ) -> None:
        assert await repo.get_latest_for_document(uuid.uuid4()) is None

    async def test_status_result_and_error_are_stored(
        self, repo: repository.MongoQualityJobRepository
    ) -> None:
        job = _make_job()
        await repo.create_job(job)

        await repo.update_status(job.id, models.JobStatus.RUNNING)
        running = await repo.get_job(job.id)
        await repo.store_result(job.id, {"comments": []})
        completed = await repo.get_job(job.id)
        await repo.set_error(job.id, "Bedrock is down")
        failed = await repo.get_job(job.id)

        assert running is not None
        assert running.status == models.JobStatus.RUNNING
        assert completed is not None
        assert completed.status == models.JobStatus.COMPLETED
        assert completed.result == {"comments": []}
        assert failed is not None
        assert failed.status == models.JobStatus.ERROR
        assert failed.error_message == "Bedrock is down"

    async def test_sections_done_are_counted_one_by_one(
        self, repo: repository.MongoQualityJobRepository
    ) -> None:
        job = _make_job()
        job.sections_total = 3
        await repo.create_job(job)

        await repo.record_section_done(job.id)
        await repo.record_section_done(job.id)
        found = await repo.get_job(job.id)

        assert found is not None
        assert found.sections_total == 3
        assert found.sections_done == 2
