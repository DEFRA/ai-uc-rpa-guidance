"""Tests for the in-process queue that runs rebuilds of the index."""

import asyncio
import uuid
from unittest.mock import AsyncMock, Mock

import pytest_mock

from app.guidance.summaries import service, worker


class TestRebuildQueue:
    async def test_runs_each_submitted_rebuild_in_turn(self) -> None:
        ran: list[uuid.UUID] = []
        first, second = uuid.uuid4(), uuid.uuid4()

        async def run(rebuild_id: uuid.UUID) -> None:
            ran.append(rebuild_id)

        queue = worker.RebuildQueue()
        queue.start(run)
        await queue.submit(first)
        await queue.submit(second)
        await queue.join()
        await queue.stop()

        assert ran == [first, second]

    async def test_keeps_working_after_a_rebuild_raises(self) -> None:
        ran: list[uuid.UUID] = []
        failing, next_one = uuid.uuid4(), uuid.uuid4()

        async def run(rebuild_id: uuid.UUID) -> None:
            if rebuild_id == failing:
                msg = "boom"
                raise RuntimeError(msg)
            ran.append(rebuild_id)

        queue = worker.RebuildQueue()
        queue.start(run)
        await queue.submit(failing)
        await queue.submit(next_one)
        await queue.join()
        await queue.stop()

        assert ran == [next_one]

    async def test_stopping_cancels_a_rebuild_in_progress(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def run(_: uuid.UUID) -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        queue = worker.RebuildQueue()
        queue.start(run)
        await queue.submit(uuid.uuid4())
        await started.wait()
        await queue.stop()

        assert cancelled.is_set()


class TestStart:
    async def test_fails_interrupted_rebuilds_then_runs_new_ones(
        self, mocker: pytest_mock.MockerFixture
    ) -> None:
        summary_service = AsyncMock(spec=service.SummaryService)
        mocker.patch.object(
            worker.dependencies,
            "summary_service_for",
            return_value=summary_service,
        )
        rebuild_id = uuid.uuid4()

        await worker.start(Mock())
        await worker.get_rebuild_queue().submit(rebuild_id)
        await worker.get_rebuild_queue().join()
        await worker.stop()

        summary_service.fail_interrupted_rebuilds.assert_awaited_once()
        summary_service.run_rebuild.assert_awaited_once_with(rebuild_id)


class TestCancel:
    async def test_cancels_the_running_rebuild_and_carries_on(self) -> None:
        running, next_one = uuid.uuid4(), uuid.uuid4()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        ran: list[uuid.UUID] = []

        async def run(rebuild_id: uuid.UUID) -> None:
            if rebuild_id == running:
                started.set()
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            ran.append(rebuild_id)

        queue = worker.RebuildQueue()
        queue.start(run)
        await queue.submit(running)
        await started.wait()

        await queue.cancel(running)

        assert cancelled.is_set()
        await queue.submit(next_one)
        await queue.join()
        await queue.stop()
        assert ran == [next_one]

    async def test_skips_a_cancelled_rebuild_still_queued(self) -> None:
        blocking, queued = uuid.uuid4(), uuid.uuid4()
        release = asyncio.Event()
        ran: list[uuid.UUID] = []

        async def run(rebuild_id: uuid.UUID) -> None:
            if rebuild_id == blocking:
                await release.wait()
            ran.append(rebuild_id)

        queue = worker.RebuildQueue()
        queue.start(run)
        await queue.submit(blocking)
        await queue.submit(queued)

        await queue.cancel(queued)
        release.set()
        await queue.join()
        await queue.stop()

        assert ran == [blocking]
