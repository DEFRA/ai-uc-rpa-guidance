"""Repository for content review (critique) jobs."""

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

import pymongo.asynchronous.database

from app.critique.jobs import models

logger = logging.getLogger(__name__)

COLLECTION_NAME = "critique_jobs"


class AbstractCritiqueJobRepository(ABC):
    """Repository interface for content review job persistence."""

    @abstractmethod
    async def create_job(self, job: models.CritiqueJob) -> models.CritiqueJob:
        """Persist a new job record.

        Args:
            job: The job to create.

        Returns:
            The created job.
        """

    @abstractmethod
    async def get_job(self, job_id: uuid.UUID) -> models.CritiqueJob | None:
        """Retrieve a job by its ID.

        Args:
            job_id: The job UUID.

        Returns:
            The job, or None if not found.
        """

    @abstractmethod
    async def get_latest_for_document(
        self, document_id: uuid.UUID
    ) -> models.CritiqueJob | None:
        """Retrieve the most recent job for a document.

        Args:
            document_id: The guidance document UUID.

        Returns:
            The latest job for the document, or None if no jobs exist.
        """

    @abstractmethod
    async def update_status(self, job_id: uuid.UUID, status: models.JobStatus) -> None:
        """Update a job's status.

        Args:
            job_id: The job UUID.
            status: The new status.
        """

    @abstractmethod
    async def store_result(self, job_id: uuid.UUID, result: dict[str, Any]) -> None:
        """Mark a job completed and store its result.

        Args:
            job_id: The job UUID.
            result: The serialised CritiqueResponse dict.
        """

    @abstractmethod
    async def set_error(self, job_id: uuid.UUID, message: str) -> None:
        """Mark a job failed and store the error message.

        Args:
            job_id: The job UUID.
            message: The error detail.
        """


class MongoCritiqueJobRepository(AbstractCritiqueJobRepository):
    """MongoDB-backed implementation of AbstractCritiqueJobRepository."""

    def __init__(
        self,
        db: pymongo.asynchronous.database.AsyncDatabase,
    ) -> None:
        """Initialise with a MongoDB database.

        Args:
            db: AsyncDatabase instance from pymongo.
        """
        self.db = db
        self.collection = db[COLLECTION_NAME]

    async def create_job(self, job: models.CritiqueJob) -> models.CritiqueJob:
        await self.collection.insert_one(job.to_document())
        logger.info("Created critique job %s", job.id)
        return job

    async def get_job(self, job_id: uuid.UUID) -> models.CritiqueJob | None:
        doc = await self.collection.find_one({"_id": job_id})
        if not doc:
            return None
        return models.CritiqueJob.from_mongo_doc(doc)

    async def get_latest_for_document(
        self, document_id: uuid.UUID
    ) -> models.CritiqueJob | None:
        cursor = (
            self.collection.find({"document_id": document_id})
            .sort("created_at", -1)
            .limit(1)
        )
        async for doc in cursor:
            return models.CritiqueJob.from_mongo_doc(doc)
        return None

    async def update_status(self, job_id: uuid.UUID, status: models.JobStatus) -> None:
        await self.collection.update_one(
            {"_id": job_id},
            {"$set": {"status": status.value, "updated_at": datetime.now(tz=UTC)}},
        )
        logger.info("Updated critique job %s status to %s", job_id, status.value)

    async def store_result(self, job_id: uuid.UUID, result: dict[str, Any]) -> None:
        await self.collection.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": models.JobStatus.COMPLETED.value,
                    "result": result,
                    "updated_at": datetime.now(tz=UTC),
                }
            },
        )
        logger.info("Stored result for critique job %s", job_id)

    async def set_error(self, job_id: uuid.UUID, message: str) -> None:
        await self.collection.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": models.JobStatus.ERROR.value,
                    "error_message": message,
                    "updated_at": datetime.now(tz=UTC),
                }
            },
        )
        logger.info("Set error for critique job %s", job_id)
