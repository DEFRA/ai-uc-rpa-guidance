"""Tests for the guidance service."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.guidance.documents import api_schemas, models, parser, service


class TestInitiateUpload:
    """Test the initiate_upload method."""

    @pytest.fixture
    def mock_repository(self) -> AsyncMock:
        """Create a mock repository."""
        return AsyncMock()

    @pytest.fixture
    def mock_parser(self) -> AsyncMock:
        """Create a mock document parser."""
        parser_mock: AsyncMock = AsyncMock(spec=parser.DocumentParser)
        return parser_mock

    @pytest.fixture
    def guidance_service(
        self, mock_repository: AsyncMock, mock_parser: AsyncMock
    ) -> service.GuidanceService:
        """Create a GuidanceService instance with mock repository and parser."""
        return service.GuidanceService(mock_repository, mock_parser)

    @pytest.mark.asyncio
    async def test_initiate_upload_success(
        self,
        guidance_service: service.GuidanceService,
        mock_repository: AsyncMock,
    ) -> None:
        """Test successful document upload initiation."""
        upload_id = "507f1f77bcf86cd799439011"

        with patch(
            "app.guidance.documents.service.http_client.create_async_client"
        ) as mock_client_factory:
            mock_response = MagicMock()
            mock_response.json.return_value = {"uploadId": upload_id}
            mock_response.raise_for_status.return_value = None

            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_factory.return_value = mock_client

            mock_repository.create_document.return_value = None

            request = api_schemas.DocumentUploadRequest(
                title="Test Document",
                description="A test document",
                redirect="http://example.com/return",
            )

            result = await guidance_service.initiate_upload(request)

            assert result == upload_id
            mock_client.post.assert_called_once()
            mock_repository.create_document.assert_called_once()

            created_doc = mock_repository.create_document.call_args[0][0]
            assert created_doc.title == "Test Document"
            assert created_doc.description == "A test document"
            assert created_doc.status == models.ExtractionStatus.PENDING

    @pytest.mark.asyncio
    async def test_initiate_upload_http_error(
        self,
        guidance_service: service.GuidanceService,
    ) -> None:
        """Test initiate_upload when CDP uploader returns an error."""
        with patch(
            "app.guidance.documents.service.http_client.create_async_client"
        ) as mock_client_factory:
            mock_response = MagicMock()
            mock_response.raise_for_status.side_effect = Exception(
                "HTTP 502 Bad Gateway"
            )

            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post.return_value = mock_response
            mock_client_factory.return_value = mock_client

            request = api_schemas.DocumentUploadRequest(
                title="Test Document",
                description="A test document",
                redirect="http://example.com/return",
            )

            with pytest.raises(Exception, match="HTTP 502 Bad Gateway"):
                await guidance_service.initiate_upload(request)


class TestGuidanceService:
    """Test the GuidanceService class."""

    @pytest.fixture
    def mock_repository(self) -> AsyncMock:
        """Create a mock repository."""
        return AsyncMock()

    @pytest.fixture
    def mock_parser(self) -> AsyncMock:
        """Create a mock document parser."""
        parser_mock: AsyncMock = AsyncMock(spec=parser.DocumentParser)
        return parser_mock

    @pytest.fixture
    def guidance_service(
        self, mock_repository: AsyncMock, mock_parser: AsyncMock
    ) -> service.GuidanceService:
        """Create a GuidanceService instance with mock repository and parser."""
        return service.GuidanceService(mock_repository, mock_parser)

    @pytest.mark.asyncio
    async def test_handle_callback_success(
        self,
        guidance_service: service.GuidanceService,
        mock_repository: AsyncMock,
        mock_parser: AsyncMock,
    ) -> None:
        """Test successful callback handling."""
        document_id = uuid.uuid4()
        document = models.GuidanceDocument(
            id=document_id,
            status=models.ExtractionStatus.PENDING,
        )

        mock_repository.get_document.return_value = document
        mock_parser.parse.return_value = parser.ParseResult(
            status=models.ExtractionStatus.COMPLETE,
            content="# Parsed content",
        )

        payload = api_schemas.CdpUploaderStatusPayload(
            upload_status="completed",
            form={
                "file1": api_schemas.FileUploadDetail(
                    file_id="file-1",
                    filename="test.pdf",
                    file_status="completed",
                    content_length=1024,
                    checksum_sha256="abc123",
                    s3_key="test.pdf",
                    s3_bucket="guidance-bucket",
                )
            },
        )

        # Capture document state at each update_document call
        captured_states: list[dict] = []

        async def capture_update(
            doc: models.GuidanceDocument,
        ) -> models.GuidanceDocument:
            captured_states.append(
                {
                    "status": doc.status,
                    "filename": doc.filename,
                    "path": doc.path,
                    "content": doc.content,
                }
            )
            return doc

        mock_repository.update_document.side_effect = capture_update

        await guidance_service.handle_callback(document_id, payload)

        assert mock_repository.update_document.call_count == 2

        # First call sets PROCESSING
        assert captured_states[0]["status"] == models.ExtractionStatus.PROCESSING
        assert captured_states[0]["filename"] == "test.pdf"
        assert captured_states[0]["path"] == "s3://guidance-bucket/test.pdf"

        # Second call persists parse result
        assert captured_states[1]["status"] == models.ExtractionStatus.COMPLETE
        assert captured_states[1]["content"] == "# Parsed content"

        mock_parser.parse.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_callback_document_not_found(
        self,
        guidance_service: service.GuidanceService,
        mock_repository: AsyncMock,
    ) -> None:
        """Test callback handling when document doesn't exist."""
        document_id = uuid.uuid4()
        mock_repository.get_document.return_value = None

        payload = api_schemas.CdpUploaderStatusPayload(
            upload_status="completed",
            form={},
        )

        with pytest.raises(ValueError, match="Document with ID .* not found"):
            await guidance_service.handle_callback(document_id, payload)

    @pytest.mark.asyncio
    async def test_list_documents_success(
        self,
        guidance_service: service.GuidanceService,
        mock_repository: AsyncMock,
    ) -> None:
        """Test successful document listing."""
        document_id = uuid.uuid4()
        document = models.GuidanceDocument(
            id=document_id,
            filename="test.pdf",
            path="s3://guidance-bucket/test.pdf",
            status=models.ExtractionStatus.PROCESSING,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )

        mock_repository.list_documents.return_value = ([document], 1)

        response = await guidance_service.list_documents(page=1, page_size=10)

        assert len(response.items) == 1
        assert response.total == 1
        assert response.page == 1
        assert response.page_size == 10
        assert response.items[0].id == str(document_id)

    @pytest.mark.asyncio
    async def test_list_documents_empty(
        self,
        guidance_service: service.GuidanceService,
        mock_repository: AsyncMock,
    ) -> None:
        """Test listing documents when none exist."""
        mock_repository.list_documents.return_value = ([], 0)

        response = await guidance_service.list_documents(page=1, page_size=10)

        assert len(response.items) == 0
        assert response.total == 0

    @pytest.mark.asyncio
    async def test_list_documents_pagination(
        self,
        guidance_service: service.GuidanceService,
        mock_repository: AsyncMock,
    ) -> None:
        """Test document listing with different pages."""
        documents = [
            models.GuidanceDocument(
                id=uuid.uuid4(),
                filename=f"test{i}.pdf",
                path=f"s3://guidance-bucket/test{i}.pdf",
                status=models.ExtractionStatus.COMPLETE,
            )
            for i in range(5)
        ]

        mock_repository.list_documents.return_value = (documents[:2], 5)

        response = await guidance_service.list_documents(page=1, page_size=2)

        assert len(response.items) == 2
        assert response.total == 5
        mock_repository.list_documents.assert_called_once_with(1, 2)
