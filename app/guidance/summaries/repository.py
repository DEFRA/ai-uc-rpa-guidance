"""MongoDB repository for guidance document summaries."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pymongo

from app.guidance.summaries import models

logger = logging.getLogger(__name__)

COLLECTION_NAME = "guidance_document_summaries"
SECTION_COLLECTION_NAME = "guidance_section_summaries"
REBUILD_COLLECTION_NAME = "guidance_index_rebuilds"

_UNFINISHED = [models.RebuildStatus.QUEUED.value, models.RebuildStatus.RUNNING.value]


class SummaryRepository:
    """Repository for persisting document summaries to MongoDB.

    The guidance document's id is the summary's `_id`, so a document can hold
    at most one summary and a rebuild replaces the previous one in place.
    """

    def __init__(
        self,
        db: pymongo.asynchronous.database.AsyncDatabase,
    ) -> None:
        """Initialize the repository with a MongoDB database.

        Args:
            db: AsyncDatabase instance from pymongo.
        """
        self.db = db
        self.collection = db[COLLECTION_NAME]

    async def save_summary(
        self, summary: models.DocumentSummary
    ) -> models.DocumentSummary:
        """Create or replace the summary for a document.

        Args:
            summary: The summary to persist.

        Returns:
            The persisted summary.
        """
        await self.collection.update_one(
            {"_id": summary.document_id},
            {
                "$set": {
                    "title": summary.title,
                    "about": summary.about,
                    "used_for": summary.used_for,
                    "keywords": summary.keywords,
                    "acronyms": [
                        {
                            "acronym": entry.acronym,
                            "expansion": entry.expansion,
                            "sections": entry.sections,
                        }
                        for entry in summary.acronyms
                    ],
                    "path": summary.path,
                    "start_path": summary.start_path,
                    "model": summary.model,
                    "content_sha256": summary.content_sha256,
                    "version_id": summary.version_id,
                    "updated_at": summary.updated_at,
                },
                "$setOnInsert": {"created_at": summary.created_at},
            },
            upsert=True,
        )

        logger.info("Saved summary for guidance document %s", summary.document_id)

        return summary

    async def get_summary(
        self, document_id: uuid.UUID
    ) -> models.DocumentSummary | None:
        """Retrieve the summary for a document.

        Args:
            document_id: The guidance document UUID.

        Returns:
            The summary, or None if the document has not been summarised.
        """
        result = await self.collection.find_one({"_id": document_id})

        if not result:
            return None

        return models.DocumentSummary.from_mongo_doc(result)

    async def delete_summary(self, document_id: uuid.UUID) -> bool:
        """Delete one document's summary.

        Args:
            document_id: The guidance document UUID.

        Returns:
            True if there was a summary to delete.
        """
        result = await self.collection.delete_one({"_id": document_id})
        return result.deleted_count == 1

    async def delete_all_summaries(self) -> int:
        """Delete every stored summary.

        Returns:
            How many summaries were deleted.
        """
        result = await self.collection.delete_many({})

        logger.info("Deleted %d summary record(s)", result.deleted_count)

        return int(result.deleted_count)

    async def list_summaries(self) -> list[models.DocumentSummary]:
        """List every summary, by title.

        A rebuild writes the whole index within seconds, so the order it was
        written in says nothing; the title is what a reader scans.

        Returns:
            All stored summaries.
        """
        cursor = self.collection.find({}).sort("title", 1)

        return [models.DocumentSummary.from_mongo_doc(doc) async for doc in cursor]


class SectionSummaryRepository:
    """Repository for persisting section index entries to MongoDB.

    The `_id` is the document id and section number together, so a section
    holds at most one entry and a rebuild replaces it in place.
    """

    def __init__(
        self,
        db: pymongo.asynchronous.database.AsyncDatabase,
    ) -> None:
        """Initialize the repository with a MongoDB database.

        Args:
            db: AsyncDatabase instance from pymongo.
        """
        self.db = db
        self.collection = db[SECTION_COLLECTION_NAME]

    async def save_sections(
        self, sections: list[models.SectionSummary]
    ) -> list[models.SectionSummary]:
        """Create or replace the entries for a document's sections.

        Args:
            sections: The section entries to persist.

        Returns:
            The persisted entries.
        """
        if not sections:
            return sections

        await self.collection.bulk_write(
            [
                pymongo.UpdateOne(
                    {"_id": section.entry_id},
                    {
                        "$set": {
                            "document_id": section.document_id,
                            "number": section.number,
                            "heading": section.heading,
                            "level": section.level,
                            "summary": section.summary,
                            "keywords": section.keywords,
                            "acronyms": [
                                {
                                    "acronym": entry.acronym,
                                    "expansion": entry.expansion,
                                }
                                for entry in section.acronyms
                            ],
                            "start_path": section.start_path,
                            "order": section.order,
                            "updated_at": section.updated_at,
                        },
                        "$setOnInsert": {"created_at": section.created_at},
                    },
                    upsert=True,
                )
                for section in sections
            ]
        )

        logger.info(
            "Saved %d section entr(y/ies) for document %s",
            len(sections),
            sections[0].document_id,
        )

        return sections

    async def delete_sections(self, document_id: uuid.UUID) -> int:
        """Delete every section entry of one document.

        Args:
            document_id: The guidance document UUID.

        Returns:
            How many entries were deleted.
        """
        result = await self.collection.delete_many({"document_id": document_id})
        return int(result.deleted_count)

    async def delete_all_sections(self) -> int:
        """Delete every stored section entry.

        Returns:
            How many entries were deleted.
        """
        result = await self.collection.delete_many({})

        logger.info("Deleted %d section entr(y/ies)", result.deleted_count)

        return int(result.deleted_count)

    async def list_sections(self) -> list[models.SectionSummary]:
        """List every section entry, in document order within each document.

        Returns:
            All stored section entries.
        """
        cursor = self.collection.find({}).sort("order", 1)

        return [models.SectionSummary.from_mongo_doc(doc) async for doc in cursor]


class RebuildRepository:
    """Repository for the rebuilds of the index and their progress."""

    def __init__(
        self,
        db: pymongo.asynchronous.database.AsyncDatabase,
    ) -> None:
        """Initialize the repository with a MongoDB database.

        Args:
            db: AsyncDatabase instance from pymongo.
        """
        self.db = db
        self.collection = db[REBUILD_COLLECTION_NAME]

    async def create_rebuild(self, rebuild: models.Rebuild) -> models.Rebuild:
        """Persist a new rebuild.

        Args:
            rebuild: The rebuild to persist.

        Returns:
            The persisted rebuild.
        """
        await self.collection.insert_one(rebuild.to_mongo_doc())
        return rebuild

    async def get_rebuild(self, rebuild_id: uuid.UUID) -> models.Rebuild | None:
        """Retrieve a rebuild.

        Args:
            rebuild_id: The rebuild's id.

        Returns:
            The rebuild, or None if there is none with this id.
        """
        doc = await self.collection.find_one({"_id": rebuild_id})
        return models.Rebuild.from_mongo_doc(doc) if doc else None

    async def find_active(self) -> models.Rebuild | None:
        """Return a rebuild that is queued or running, if there is one.

        Returns:
            The unfinished rebuild, or None.
        """
        doc = await self.collection.find_one({"status": {"$in": _UNFINISHED}})
        return models.Rebuild.from_mongo_doc(doc) if doc else None

    async def begin(
        self, rebuild_id: uuid.UUID, total: int, purged: int, skipped: int = 0
    ) -> None:
        """Mark a rebuild running, with how many guides it covers.

        The guides already up to date count as completed from the start, so
        the progress fast-forwards past them.

        Args:
            rebuild_id: The rebuild's id.
            total: How many guides it covers, skipped ones included.
            purged: How many summaries it discarded.
            skipped: How many guides it left alone, already up to date.
        """
        await self._set(
            rebuild_id,
            {
                "status": models.RebuildStatus.RUNNING.value,
                "total": total,
                "completed": skipped,
                "purged": purged,
                "skipped": skipped,
            },
        )

    async def start_guide(self, rebuild_id: uuid.UUID, title: str) -> None:
        """Record the guide a rebuild has started last.

        Args:
            rebuild_id: The rebuild's id.
            title: The guide's title.
        """
        await self._set(rebuild_id, {"current_title": title})

    async def finish_guide(
        self, rebuild_id: uuid.UUID, failure: models.RebuildFailure | None
    ) -> None:
        """Count a guide as finished, recording why if it failed.

        Args:
            rebuild_id: The rebuild's id.
            failure: Why the guide could not be indexed, or None if it was.
        """
        update: dict[str, Any] = {
            "$inc": {"completed": 1},
            "$set": {"updated_at": datetime.now(tz=UTC)},
        }
        if failure is not None:
            update["$push"] = {"failures": vars(failure)}

        await self.collection.update_one({"_id": rebuild_id}, update)

    async def complete(self, rebuild_id: uuid.UUID, duration_seconds: float) -> None:
        """Mark a rebuild complete.

        Args:
            rebuild_id: The rebuild's id.
            duration_seconds: How long it took, wall clock.
        """
        await self._set(
            rebuild_id,
            {
                "status": models.RebuildStatus.COMPLETE.value,
                "duration_seconds": duration_seconds,
            },
        )

    async def fail(self, rebuild_id: uuid.UUID, error_message: str) -> None:
        """Mark a rebuild failed as a whole.

        Args:
            rebuild_id: The rebuild's id.
            error_message: Why it failed.
        """
        await self._set(
            rebuild_id,
            {
                "status": models.RebuildStatus.FAILED.value,
                "error_message": error_message,
            },
        )

    async def cancel(self, rebuild_id: uuid.UUID, duration_seconds: float) -> bool:
        """Mark a rebuild cancelled, if it is still queued or running.

        Args:
            rebuild_id: The rebuild's id.
            duration_seconds: How long it had been going, wall clock.

        Returns:
            True if it was cancelled; False if it had already finished.
        """
        result = await self.collection.update_one(
            {"_id": rebuild_id, "status": {"$in": _UNFINISHED}},
            {
                "$set": {
                    "status": models.RebuildStatus.CANCELLED.value,
                    "duration_seconds": duration_seconds,
                    "updated_at": datetime.now(tz=UTC),
                }
            },
        )
        return result.modified_count == 1

    async def fail_unfinished(self, error_message: str) -> int:
        """Mark every queued or running rebuild failed.

        Args:
            error_message: Why they failed.

        Returns:
            How many rebuilds were failed.
        """
        result = await self.collection.update_many(
            {"status": {"$in": _UNFINISHED}},
            {
                "$set": {
                    "status": models.RebuildStatus.FAILED.value,
                    "error_message": error_message,
                    "updated_at": datetime.now(tz=UTC),
                }
            },
        )
        return int(result.modified_count)

    async def _set(self, rebuild_id: uuid.UUID, fields: dict[str, Any]) -> None:
        """Set fields on a rebuild, and when it was last updated."""
        await self.collection.update_one(
            {"_id": rebuild_id},
            {"$set": {**fields, "updated_at": datetime.now(tz=UTC)}},
        )
