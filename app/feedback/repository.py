"""Repository for feedback entries."""

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

import pymongo.asynchronous.database

from app.feedback import models

logger = logging.getLogger(__name__)

COLLECTION_NAME = "feedback"


class AbstractFeedbackRepository(ABC):
    """Repository interface for feedback entry persistence."""

    @abstractmethod
    async def create(self, entry: models.FeedbackEntry) -> models.FeedbackEntry:
        """Persist a new feedback entry.

        Args:
            entry: The entry to create.

        Returns:
            The created entry.
        """

    @abstractmethod
    async def get_by_id(self, feedback_id: uuid.UUID) -> models.FeedbackEntry | None:
        """Retrieve a feedback entry by its ID.

        Args:
            feedback_id: The feedback UUID.

        Returns:
            The entry, or None if not found.
        """

    @abstractmethod
    async def get_for_job(self, job_id: uuid.UUID) -> list[models.FeedbackEntry]:
        """Retrieve all feedback entries for a job, ordered by creation time.

        Args:
            job_id: The job UUID.

        Returns:
            List of entries (empty if none exist).
        """

    @abstractmethod
    async def get_for_finding(
        self, job_id: uuid.UUID, finding_index: int | None
    ) -> models.FeedbackEntry | None:
        """Retrieve feedback for a specific finding (or job-level if None).

        Args:
            job_id: The job UUID.
            finding_index: Finding index, or None for job-level feedback.

        Returns:
            The entry, or None if no feedback exists for this job+finding.
        """

    @abstractmethod
    async def update(
        self,
        feedback_id: uuid.UUID,
        verdict: models.FeedbackVerdict | None,
        comment: str | None,
    ) -> models.FeedbackEntry | None:
        """Update verdict and/or comment on a feedback entry.

        Args:
            feedback_id: The feedback UUID.
            verdict: New verdict, or None to leave unchanged.
            comment: New comment, or None to leave unchanged.

        Returns:
            The updated entry, or None if not found.
        """


class MongoFeedbackRepository(AbstractFeedbackRepository):
    """MongoDB-backed implementation of AbstractFeedbackRepository."""

    def __init__(self, db: pymongo.asynchronous.database.AsyncDatabase) -> None:
        """Initialise with a MongoDB database.

        Args:
            db: AsyncDatabase instance from pymongo.
        """
        self.db = db
        self.collection = db[COLLECTION_NAME]

    async def create(self, entry: models.FeedbackEntry) -> models.FeedbackEntry:
        await self.collection.insert_one(entry.to_document())
        logger.info("Created feedback entry %s", entry.id)
        return entry

    async def get_by_id(self, feedback_id: uuid.UUID) -> models.FeedbackEntry | None:
        doc = await self.collection.find_one({"_id": feedback_id})
        if not doc:
            return None
        return models.FeedbackEntry.from_mongo_doc(doc)

    async def get_for_job(self, job_id: uuid.UUID) -> list[models.FeedbackEntry]:
        cursor = self.collection.find({"job_id": job_id}).sort("created_at", 1)
        return [models.FeedbackEntry.from_mongo_doc(doc) async for doc in cursor]

    async def get_for_finding(
        self, job_id: uuid.UUID, finding_index: int | None
    ) -> models.FeedbackEntry | None:
        doc = await self.collection.find_one(
            {"job_id": job_id, "finding_index": finding_index}
        )
        if not doc:
            return None
        return models.FeedbackEntry.from_mongo_doc(doc)

    async def update(
        self,
        feedback_id: uuid.UUID,
        verdict: models.FeedbackVerdict | None,
        comment: str | None,
    ) -> models.FeedbackEntry | None:
        updates: dict[str, Any] = {"updated_at": datetime.now(tz=UTC)}
        if verdict is not None:
            updates["verdict"] = verdict.value
        if comment is not None:
            updates["comment"] = comment

        doc = await self.collection.find_one_and_update(
            {"_id": feedback_id},
            {"$set": updates},
            return_document=pymongo.ReturnDocument.AFTER,
        )
        if not doc:
            return None
        logger.info("Updated feedback entry %s", feedback_id)
        return models.FeedbackEntry.from_mongo_doc(doc)
