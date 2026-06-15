"""Pipeline trigger protocol and implementations for guidance document processing."""

import logging
from typing import Protocol

import fastapi

from app.guidance.documents import models, parser, repository

logger = logging.getLogger(__name__)


class DocumentPipelineTrigger(Protocol):
    """Protocol for triggering the document processing pipeline."""

    async def trigger(self, document: models.GuidanceDocument) -> None:
        """Trigger processing for a guidance document.

        Args:
            document: The guidance document to process.
        """
        ...


class PipelineExecutor:
    """Runs the parse pipeline and persists the result."""

    def __init__(
        self,
        doc_parser: parser.DocumentParser,
        repo: repository.GuidanceRepository,
    ) -> None:
        """Initialise with a parser and repository.

        Args:
            doc_parser: Parser that downloads, converts, and stores the document.
            repo: Repository used to persist the parse result.
        """
        self._parser = doc_parser
        self._repo = repo

    async def execute(self, document: models.GuidanceDocument) -> None:
        """Parse a document and persist the result.

        On failure the result's FAILED status and error_message are saved;
        the method itself never raises.

        Args:
            document: The guidance document to parse (must have a path set).
        """
        result = await self._parser.parse(document)
        document.status = result.status
        document.content = result.content
        if result.title is not None:
            document.title = result.title
        document.error_message = result.error_message
        await self._repo.update_document(document)
        logger.info("Pipeline execution complete for document %s", document.id)


class BackgroundTaskPipelineTrigger:
    """Triggers document processing as a FastAPI background task."""

    def __init__(
        self,
        background_tasks: fastapi.BackgroundTasks,
        executor: PipelineExecutor,
    ) -> None:
        """Initialise with FastAPI background task queue and executor.

        Args:
            background_tasks: Request-scoped FastAPI BackgroundTasks.
            executor: Executor that runs the parse pipeline.
        """
        self._background_tasks = background_tasks
        self._executor = executor

    async def trigger(self, document: models.GuidanceDocument) -> None:
        """Enqueue document processing as a FastAPI background task.

        Args:
            document: The guidance document to process.
        """
        self._background_tasks.add_task(self._executor.execute, document)
