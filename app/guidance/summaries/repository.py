"""MongoDB repository for guidance document summaries."""

import logging
import uuid

import pymongo

from app.guidance.summaries import models

logger = logging.getLogger(__name__)

COLLECTION_NAME = "guidance_document_summaries"
SECTION_COLLECTION_NAME = "guidance_section_summaries"


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
