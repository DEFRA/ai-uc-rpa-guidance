"""Integration tests for RebuildRepository using TestContainers."""

import uuid
from collections.abc import AsyncGenerator, Generator

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
    db = client["test_rebuilds"]
    yield db
    await client.drop_database("test_rebuilds")
    await client.close()


@pytest.fixture
def repo(
    mongo_db: pymongo.asynchronous.database.AsyncDatabase,
) -> repository.RebuildRepository:
    return repository.RebuildRepository(mongo_db)


async def _created(repo: repository.RebuildRepository) -> models.Rebuild:
    return await repo.create_rebuild(models.Rebuild(rebuild_id=uuid.uuid4()))


class TestCreate:
    async def test_a_new_rebuild_is_queued_with_nothing_done(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)

        stored = await repo.get_rebuild(rebuild.rebuild_id)
        assert stored is not None
        assert stored.status is models.RebuildStatus.QUEUED
        assert stored.total == 0
        assert stored.completed == 0
        assert stored.current_title is None
        assert stored.failures == []
        assert stored.duration_seconds is None

    async def test_an_unknown_rebuild_is_none(
        self, repo: repository.RebuildRepository
    ) -> None:
        assert await repo.get_rebuild(uuid.uuid4()) is None


class TestMode:
    async def test_records_whether_the_rebuild_is_full_or_partial(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await repo.create_rebuild(
            models.Rebuild(rebuild_id=uuid.uuid4(), mode=models.RebuildMode.PARTIAL)
        )

        stored = await repo.get_rebuild(rebuild.rebuild_id)

        assert stored is not None
        assert stored.mode is models.RebuildMode.PARTIAL

    async def test_a_rebuild_is_full_unless_asked(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)

        stored = await repo.get_rebuild(rebuild.rebuild_id)

        assert stored is not None
        assert stored.mode is models.RebuildMode.FULL


class TestProgress:
    async def test_beginning_records_the_total_and_what_was_purged(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)

        await repo.begin(rebuild.rebuild_id, total=17, purged=3)

        stored = await repo.get_rebuild(rebuild.rebuild_id)
        assert stored is not None
        assert stored.status is models.RebuildStatus.RUNNING
        assert (stored.total, stored.purged, stored.skipped) == (17, 3, 0)

    async def test_counts_the_guides_already_up_to_date_as_completed(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)

        await repo.begin(rebuild.rebuild_id, total=16, purged=1, skipped=14)
        await repo.finish_guide(rebuild.rebuild_id, None)

        stored = await repo.get_rebuild(rebuild.rebuild_id)
        assert stored is not None
        assert (stored.total, stored.skipped, stored.completed) == (16, 14, 15)

    async def test_records_the_guide_started_last(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)

        await repo.start_guide(rebuild.rebuild_id, "First guide")
        await repo.start_guide(rebuild.rebuild_id, "Second guide")

        stored = await repo.get_rebuild(rebuild.rebuild_id)
        assert stored is not None
        assert stored.current_title == "Second guide"

    async def test_counts_every_finished_guide_and_keeps_each_failure(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)
        failure = models.RebuildFailure(
            document_id="abc", title="A guide", error_message="Bedrock said no"
        )

        await repo.finish_guide(rebuild.rebuild_id, None)
        await repo.finish_guide(rebuild.rebuild_id, failure)

        stored = await repo.get_rebuild(rebuild.rebuild_id)
        assert stored is not None
        assert stored.completed == 2
        assert stored.failures == [failure]

    async def test_completing_records_how_long_it_took(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)

        await repo.complete(rebuild.rebuild_id, duration_seconds=12.5)

        stored = await repo.get_rebuild(rebuild.rebuild_id)
        assert stored is not None
        assert stored.status is models.RebuildStatus.COMPLETE
        assert stored.duration_seconds == 12.5

    async def test_failing_records_why(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)

        await repo.fail(rebuild.rebuild_id, "No manifest")

        stored = await repo.get_rebuild(rebuild.rebuild_id)
        assert stored is not None
        assert stored.status is models.RebuildStatus.FAILED
        assert stored.error_message == "No manifest"


class TestActive:
    async def test_finds_a_queued_or_running_rebuild(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)

        active = await repo.find_active()

        assert active is not None
        assert active.rebuild_id == rebuild.rebuild_id

    async def test_a_finished_rebuild_is_not_active(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)
        await repo.complete(rebuild.rebuild_id, duration_seconds=1.0)

        assert await repo.find_active() is None

    async def test_fails_every_unfinished_rebuild(
        self, repo: repository.RebuildRepository
    ) -> None:
        queued = await _created(repo)
        running = await _created(repo)
        await repo.begin(running.rebuild_id, total=1, purged=0)
        finished = await _created(repo)
        await repo.complete(finished.rebuild_id, duration_seconds=1.0)

        failed = await repo.fail_unfinished("Interrupted by restart")

        assert failed == 2
        assert await repo.find_active() is None
        stored = await repo.get_rebuild(finished.rebuild_id)
        assert stored is not None
        assert stored.status is models.RebuildStatus.COMPLETE
        stored = await repo.get_rebuild(queued.rebuild_id)
        assert stored is not None
        assert stored.error_message == "Interrupted by restart"


class TestCancel:
    async def test_cancels_an_unfinished_rebuild_with_how_long_it_ran(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)
        await repo.begin(rebuild.rebuild_id, total=3, purged=0)

        cancelled = await repo.cancel(rebuild.rebuild_id, duration_seconds=4.5)

        assert cancelled is True
        stored = await repo.get_rebuild(rebuild.rebuild_id)
        assert stored is not None
        assert stored.status is models.RebuildStatus.CANCELLED
        assert stored.duration_seconds == 4.5
        assert await repo.find_active() is None

    async def test_leaves_a_finished_rebuild_as_it_is(
        self, repo: repository.RebuildRepository
    ) -> None:
        rebuild = await _created(repo)
        await repo.complete(rebuild.rebuild_id, duration_seconds=1.0)

        cancelled = await repo.cancel(rebuild.rebuild_id, duration_seconds=9.0)

        assert cancelled is False
        stored = await repo.get_rebuild(rebuild.rebuild_id)
        assert stored is not None
        assert stored.status is models.RebuildStatus.COMPLETE
