"""Tests for PublishingJobService."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.publishing import models as publishing_models
from app.publishing.jobs import documents, models, service

_SECTIONS = [
    publishing_models.DocumentSection(number="1", text="## 1 One\n\nSome content.\n"),
    publishing_models.DocumentSection(number="2", text="## 2 Two\n\nMore content.\n"),
]


def _make_ready_content(doc_id: uuid.UUID | None = None) -> documents.DocumentContent:
    return documents.DocumentContent(
        document_id=doc_id or uuid.uuid4(),
        title="Test Guidance",
        sections=_SECTIONS,
        ready=True,
    )


def _make_not_ready_content(
    doc_id: uuid.UUID | None = None,
) -> documents.DocumentContent:
    return documents.DocumentContent(
        document_id=doc_id or uuid.uuid4(),
        title="Test Guidance",
        sections=[],
        ready=False,
    )


def _make_service(
    content: documents.DocumentContent | None = None,
    submitted_jobs: list[models.AnalysisJob] | None = None,
) -> service.PublishingJobService:
    job_repo = AsyncMock()
    job_repo.create_job = AsyncMock(side_effect=lambda j: j)
    job_repo.get_job = AsyncMock(return_value=None)
    job_repo.get_latest_for_document = AsyncMock(return_value=None)

    content_source = AsyncMock()
    content_source.get = AsyncMock(return_value=content)

    captured: list[models.AnalysisJob] = (
        submitted_jobs if submitted_jobs is not None else []
    )
    job_submitter = AsyncMock()

    async def _capture_submit(job: models.AnalysisJob) -> None:
        captured.append(job)

    job_submitter.submit = AsyncMock(side_effect=_capture_submit)

    return service.PublishingJobService(job_repo, content_source, job_submitter)


class TestStartAnalysis:
    async def test_raises_document_not_found_when_content_source_returns_none(
        self,
    ) -> None:
        svc = _make_service(content=None)

        with pytest.raises(service.DocumentNotFoundError):
            await svc.start_analysis(uuid.uuid4())

    async def test_raises_document_not_ready_when_content_not_ready(self) -> None:
        svc = _make_service(content=_make_not_ready_content())

        with pytest.raises(service.DocumentNotReadyError):
            await svc.start_analysis(uuid.uuid4())

    async def test_returns_pending_job_on_success(self) -> None:
        svc = _make_service(content=_make_ready_content())

        job = await svc.start_analysis(uuid.uuid4())

        assert job.status == models.JobStatus.PENDING

    async def test_job_links_to_document_id(self) -> None:
        doc_id = uuid.uuid4()
        svc = _make_service(content=_make_ready_content(doc_id))

        job = await svc.start_analysis(doc_id)

        assert job.document_id == doc_id

    async def test_submits_job_with_title_and_sections(self) -> None:
        doc_id = uuid.uuid4()
        submitted: list[models.AnalysisJob] = []
        svc = _make_service(
            content=_make_ready_content(doc_id), submitted_jobs=submitted
        )

        job = await svc.start_analysis(doc_id)

        assert len(submitted) == 1
        assert submitted[0].job_id == job.id
        assert submitted[0].document_id == doc_id
        assert submitted[0].document_title == "Test Guidance"
        assert submitted[0].sections == _SECTIONS

    async def test_falls_back_to_placeholder_title_when_missing(self) -> None:
        doc_id = uuid.uuid4()
        submitted: list[models.AnalysisJob] = []
        content = _make_ready_content(doc_id)
        content.title = None
        svc = _make_service(content=content, submitted_jobs=submitted)

        await svc.start_analysis(doc_id)

        assert submitted[0].document_title == "Untitled document"


class TestGetJob:
    async def test_returns_none_when_job_not_found(self) -> None:
        svc = _make_service()

        result = await svc.get_job(uuid.uuid4())

        assert result is None


class TestGetLatestForDocument:
    async def test_returns_none_when_no_jobs_exist(self) -> None:
        svc = _make_service()

        result = await svc.get_latest_for_document(uuid.uuid4())

        assert result is None
