"""Tests for QualityJobService."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.quality import anchors
from app.quality.jobs import documents, models, service

DOCUMENT_ID = uuid.uuid4()
VERSION_ID = "ad1680a5-b12f-4b1f-9b7d-6ec1539c6fdf"

CONTENT = documents.GuideContent(
    document_id=DOCUMENT_ID,
    version_id=VERSION_ID,
    title="Land use guide",
    parts=anchors.top_level_parts("# T\n\n## 1 One\n\nA.\n"),
)


def _make_service(
    content: documents.GuideContent | None = CONTENT,
) -> tuple[service.QualityJobService, AsyncMock, list[models.CheckJob]]:
    job_repo = AsyncMock()
    job_repo.create_job = AsyncMock(side_effect=lambda job: job)

    content_source = AsyncMock()
    content_source.get = AsyncMock(return_value=content)

    submitted: list[models.CheckJob] = []

    async def _capture(job: models.CheckJob) -> None:
        submitted.append(job)

    job_submitter = AsyncMock()
    job_submitter.submit = AsyncMock(side_effect=_capture)

    return (
        service.QualityJobService(job_repo, content_source, job_submitter),
        job_repo,
        submitted,
    )


class TestStartCheck:
    async def test_a_guide_that_does_not_exist_is_not_found(self) -> None:
        svc, _, _ = _make_service(content=None)
        document_id = uuid.uuid4()

        with pytest.raises(service.GuideNotFoundError):
            await svc.start_check(document_id)

    async def test_creates_a_pending_job_for_the_latest_version(self) -> None:
        svc, job_repo, _ = _make_service()

        job = await svc.start_check(DOCUMENT_ID)

        assert job.status == models.JobStatus.PENDING
        assert job.document_id == DOCUMENT_ID
        assert job.version_id == VERSION_ID
        assert job.sections_total == 1
        assert job.sections_done == 0
        job_repo.create_job.assert_awaited_once_with(job)

    async def test_submits_the_guide_for_checking(self) -> None:
        svc, _, submitted = _make_service()

        job = await svc.start_check(DOCUMENT_ID)

        [check] = submitted
        assert check.job_id == job.id
        assert check.document_title == "Land use guide"
        assert check.parts == CONTENT.parts


class TestReadJobs:
    async def test_gets_a_job_by_its_id(self) -> None:
        svc, job_repo, _ = _make_service()
        job_id = uuid.uuid4()

        await svc.get_job(job_id)

        job_repo.get_job.assert_awaited_once_with(job_id)

    async def test_gets_the_latest_job_for_a_guide(self) -> None:
        svc, job_repo, _ = _make_service()

        await svc.get_latest_for_document(DOCUMENT_ID)

        job_repo.get_latest_for_document.assert_awaited_once_with(DOCUMENT_ID)
