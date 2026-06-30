"""Tests for critique jobs dependency-injection providers."""

from unittest.mock import AsyncMock, MagicMock

from app.critique.jobs import dependencies, documents, repository, service, submitter


def test_get_job_repository_returns_mongo_repository() -> None:
    repo = dependencies.get_job_repository(MagicMock())
    assert isinstance(repo, repository.MongoCritiqueJobRepository)


def test_get_document_content_source_returns_guidance_adapter() -> None:
    source = dependencies.get_document_content_source(MagicMock(), MagicMock())
    assert isinstance(source, documents.GuidanceDocumentContentSource)


def test_get_review_executor_returns_executor() -> None:
    executor = dependencies.get_review_executor(AsyncMock())
    assert isinstance(executor, submitter.ReviewExecutor)


def test_get_job_submitter_returns_background_submitter() -> None:
    job_submitter = dependencies.get_job_submitter(MagicMock(), AsyncMock())
    assert isinstance(job_submitter, submitter.BackgroundTaskSubmitter)


def test_get_critique_job_service_returns_service() -> None:
    svc = dependencies.get_critique_job_service(AsyncMock(), AsyncMock(), AsyncMock())
    assert isinstance(svc, service.CritiqueJobService)
