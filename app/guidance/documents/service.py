"""Business logic service for guidance document management."""

import logging
import uuid
from datetime import UTC, datetime

from app import config
from app.common import http_client
from app.guidance.documents import api_schemas, models, parser, repository

logger = logging.getLogger(__name__)

settings = config.get_config()


class GuidanceService:
    """Service for managing guidance document uploads and processing."""

    def __init__(
        self,
        repo: repository.GuidanceRepository,
        doc_parser: parser.DocumentParser,
    ) -> None:
        """Initialize the service with a repository and parser.

        Args:
            repo: The guidance repository for persistence.
            doc_parser: The document parser for triggering extraction.
        """
        self.repository = repo
        self.parser = doc_parser

    async def initiate_upload(self, request: api_schemas.DocumentUploadRequest) -> str:
        """Initiate a document upload session.

        Args:
            request: Upload configuration from the client.

        Returns:
            The upload_id for the frontend to use.

        Raises:
            httpx.HTTPStatusError: If the CDP uploader service returns an error.
        """
        document_id = uuid.uuid4()

        async with http_client.create_async_client(
            settings.cdp_uploader_timeout
        ) as client:
            resp = await client.post(
                f"{settings.cdp_uploader_base_url}/initiate",
                json={
                    "redirect": request.redirect,
                    "s3Bucket": settings.guidance_s3_bucket,
                    "s3Path": "original_docs",
                    "callback": f"{settings.callback_base_url}/guidance/documents/{document_id}/callback",
                    "metadata": {"document_id": str(document_id)},
                },
            )

            resp.raise_for_status()

            data = api_schemas.DocumentUploadResponse(**resp.json())

            document = models.GuidanceDocument(
                id=document_id,
                title=request.title,
                description=request.description,
                status=models.ExtractionStatus.PENDING,
                created_at=datetime.now(tz=UTC),
            )

            await self.repository.create_document(document)

            logger.info(
                "Initiated upload session %s for document %s",
                data.upload_id,
                document_id,
            )

            return data.upload_id

    async def handle_callback(
        self,
        document_id: uuid.UUID,
        payload: api_schemas.CdpUploaderStatusPayload,
    ) -> None:
        """Process callback from CDP uploader service.

        Args:
            document_id: The document ID to update.
            payload: The callback payload from the uploader.
        """
        document = await self.repository.get_document(document_id)

        if not document:
            msg = f"Document with ID {document_id} not found"
            raise ValueError(msg)

        for form_value in payload.form.values():
            if isinstance(form_value, api_schemas.FileUploadDetail):
                document.filename = form_value.filename
                document.status = models.ExtractionStatus.PROCESSING
                document.content_hash = form_value.checksum_sha256
                document.path = f"s3://{form_value.s3_bucket}/{form_value.s3_key}"

                await self.repository.update_document(document)

                result = await self.parser.parse(document)
                document.status = result.status
                document.content = result.content
                if result.title is not None:
                    document.title = result.title
                document.error_message = result.error_message
                await self.repository.update_document(document)

                logger.info(
                    "Callback processed and parse triggered for document %s",
                    document.id,
                )

                # Only process the first file upload detail found in the form
                return

    async def list_documents(
        self,
        page: int = 1,
        page_size: int = 10,
    ) -> api_schemas.DocumentListResponse:
        """List all guidance documents with pagination.

        Args:
            page: Page number (1-based).
            page_size: Number of items per page.

        Returns:
            Paginated list of documents.
        """
        documents, total = await self.repository.list_documents(page, page_size)

        items = []
        for doc in documents:
            items.append(
                api_schemas.DocumentResponse(
                    id=str(doc.id),
                    title=doc.title,
                    path=doc.path,
                    filename=doc.filename,
                    status=doc.status.value,
                    content_hash=doc.content_hash,
                    content=doc.content,
                    created_at=doc.created_at,
                    updated_at=doc.updated_at,
                    error_message=doc.error_message,
                )
            )

        return api_schemas.DocumentListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )
