"""MongoDB repository for guidance documents."""

import logging

import bson
import pymongo

from app.guidance.documents import models

logger = logging.getLogger(__name__)

COLLECTION_NAME = "guidance_documents"


class GuidanceRepository:
    """Repository for persisting guidance documents to MongoDB."""

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

    async def create_document(
        self, document: models.GuidanceDocument
    ) -> models.GuidanceDocument:
        """Create a new guidance document.

        Args:
            document: The guidance document to persist.

        Returns:
            The persisted document with its generated ID set.
        """
        object_id = bson.ObjectId()
        document.id = str(object_id)

        doc_dict = {
            "_id": object_id,
            "title": document.title,
            "description": document.description,
            "filename": document.filename,
            "path": document.path,
            "status": document.status.value,
            "content_hash": document.content_hash,
            "created_at": document.created_at,
            "updated_at": document.updated_at,
            "error_message": document.error_message,
        }

        result = await self.collection.insert_one(doc_dict)
        logger.info("Created guidance document %s", result.inserted_id)

        return document

    async def get_document(
        self,
        document_id: str,
    ) -> models.GuidanceDocument | None:
        """Retrieve a guidance document by ID.

        Args:
            document_id: The document ID (MongoDB ObjectId hex string).

        Returns:
            The guidance document or None if not found.
        """
        result = await self.collection.find_one({"_id": bson.ObjectId(document_id)})

        if not result:
            return None

        return models.GuidanceDocument.from_mongo_doc(result)

    async def update_document(
        self,
        document: models.GuidanceDocument,
    ) -> models.GuidanceDocument:
        """Update an existing guidance document.

        Args:
            document: The document with updated fields.

        Returns:
            The updated document.
        """
        from datetime import UTC, datetime

        document.updated_at = datetime.now(tz=UTC)

        doc_dict = {
            "filename": document.filename,
            "path": document.path,
            "status": document.status.value,
            "content_hash": document.content_hash,
            "updated_at": document.updated_at,
            "error_message": document.error_message,
        }

        await self.collection.update_one(
            {"_id": bson.ObjectId(document.id)},
            {"$set": doc_dict},
        )
        logger.info("Updated guidance document %s", document.id)

        return document

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 10,
    ) -> tuple[list[models.GuidanceDocument], int]:
        """List all guidance documents with pagination.

        Args:
            page: Page number (1-based).
            page_size: Number of items per page.

        Returns:
            Tuple of (documents, total_count).
        """
        skip = (page - 1) * page_size

        cursor = (
            self.collection.find({}).sort("created_at", -1).skip(skip).limit(page_size)
        )

        documents = []
        async for doc in cursor:
            documents.append(models.GuidanceDocument.from_mongo_doc(doc))

        total = await self.collection.count_documents({})

        return documents, total
