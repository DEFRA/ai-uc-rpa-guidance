"""The in-process queue that runs rebuilds of the index in the background.

A rebuild takes minutes — two model calls per guide — so the request that asks
for one only queues it, and a single worker started with the app runs it. One
worker means one rebuild at a time, in the order they were asked for.

The queue is held in memory: a rebuild queued or running when the service
stops is lost, so at startup any left unfinished are failed (see
SummaryService.fail_interrupted_rebuilds). Locally, the API restarts whenever
its source changes, which interrupts a rebuild in the same way.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

import pymongo

from app.guidance.summaries import dependencies

logger = logging.getLogger(__name__)

type RunRebuild = Callable[[uuid.UUID], Coroutine[Any, Any, None]]


class RebuildQueue:
    """A queue of rebuild ids, worked through by one background task."""

    def __init__(self) -> None:
        """Initialise the queue, not yet started."""
        self._queue: asyncio.Queue[uuid.UUID] | None = None
        self._task: asyncio.Task[None] | None = None
        # The rebuild being run now, and its task, so that it can be cancelled.
        self._running: tuple[uuid.UUID, asyncio.Task[None]] | None = None
        # Rebuilds cancelled while still queued, for the worker to skip.
        self._skipped: set[uuid.UUID] = set()

    def start(self, run: RunRebuild) -> None:
        """Start the worker on the running event loop.

        Args:
            run: What runs one rebuild, given its id.
        """
        self._queue = asyncio.Queue()
        self._task = asyncio.create_task(self._work(self._queue, run))

    async def cancel(self, rebuild_id: uuid.UUID) -> None:
        """Stop a rebuild: cancel it if running, or skip it if still queued.

        Returns once a running rebuild has stopped, so nothing more is written
        for it afterwards. The worker carries on with the next in the queue.

        Args:
            rebuild_id: The rebuild to stop.
        """
        if self._running is not None and self._running[0] == rebuild_id:
            task = self._running[1]
            task.cancel()
            await asyncio.wait([task])
            return

        self._skipped.add(rebuild_id)

    async def submit(self, rebuild_id: uuid.UUID) -> None:
        """Queue a rebuild for the worker to run.

        Args:
            rebuild_id: The rebuild to run.

        Raises:
            RuntimeError: If the queue has not been started.
        """
        if self._queue is None:
            msg = "The rebuild queue has not been started"
            raise RuntimeError(msg)

        await self._queue.put(rebuild_id)

    async def join(self) -> None:
        """Wait until every queued rebuild has been run."""
        if self._queue is not None:
            await self._queue.join()

    async def stop(self) -> None:
        """Stop the worker, cancelling any rebuild in progress."""
        if self._task is None:
            return

        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

        self._task = None
        self._queue = None

    async def _work(self, queue: asyncio.Queue[uuid.UUID], run: RunRebuild) -> None:
        """Run each queued rebuild in turn, for as long as the app runs.

        Each rebuild runs as its own task, so that it can be cancelled without
        cancelling the worker.
        """
        worker = asyncio.current_task()

        while True:
            rebuild_id = await queue.get()
            try:
                if rebuild_id in self._skipped:
                    self._skipped.discard(rebuild_id)
                    continue

                task = asyncio.create_task(run(rebuild_id))
                self._running = (rebuild_id, task)
                await task
            except asyncio.CancelledError:
                if worker is not None and worker.cancelling():
                    raise
                logger.info("[Summary] Rebuild %s cancelled", rebuild_id)
            except Exception:  # noqa: BLE001 - one rebuild must not stop the worker
                logger.exception("[Summary] Rebuild %s raised", rebuild_id)
            finally:
                self._running = None
                queue.task_done()


_rebuild_queue = RebuildQueue()


def get_rebuild_queue() -> RebuildQueue:
    """Get the app's rebuild queue.

    Returns:
        The one RebuildQueue the worker serves.
    """
    return _rebuild_queue


async def start(db: pymongo.asynchronous.database.AsyncDatabase) -> None:
    """Fail the rebuilds a restart interrupted, then start the worker.

    Args:
        db: MongoDB database instance.
    """
    summary_service = dependencies.summary_service_for(db)
    await summary_service.fail_interrupted_rebuilds()
    _rebuild_queue.start(summary_service.run_rebuild)


async def stop() -> None:
    """Stop the worker."""
    await _rebuild_queue.stop()
