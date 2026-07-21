"""FastAPI dependency injection for the review jobs domain."""

from typing import Annotated

import fastapi
import pymongo

from app.common import mongo
from app.guidance.documents import dependencies as guidance_dependencies
from app.guidance.documents import repository as guidance_repository
from app.guidance.documents import s3_repository
from app.review.jobs import documents, repository, service, submitter


def get_job_repository(
    db: Annotated[
        pymongo.asynchronous.database.AsyncDatabase,
        fastapi.Depends(mongo.get_db),
    ],
) -> repository.AbstractReviewJobRepository:
    """Provide a review job repository.

    Args:
        db: MongoDB database instance.

    Returns:
        MongoDB-backed job repository.
    """
    return repository.MongoReviewJobRepository(db)


def get_document_content_source(
    guidance_repo: Annotated[
        guidance_repository.GuidanceRepository,
        fastapi.Depends(guidance_dependencies.get_guidance_repository),
    ],
    storage_repo: Annotated[
        s3_repository.GuidanceS3Repository,
        fastapi.Depends(guidance_dependencies.get_s3_repository),
    ],
) -> documents.DocumentContentSource:
    """Provide a document content source backed by the guidance sub-domain.

    Args:
        guidance_repo: Guidance MongoDB repository.
        storage_repo: Guidance S3 storage repository.

    Returns:
        GuidanceDocumentContentSource adapter.
    """
    return documents.GuidanceDocumentContentSource(guidance_repo, storage_repo)


def get_review_executor(
    job_repo: Annotated[
        repository.AbstractReviewJobRepository,
        fastapi.Depends(get_job_repository),
    ],
) -> submitter.ReviewExecutor:
    """Provide a review executor.

    Args:
        job_repo: Job repository for status transitions.

    Returns:
        ReviewExecutor instance.
    """
    return submitter.ReviewExecutor(job_repo)


def get_job_submitter(
    background_tasks: fastapi.BackgroundTasks,
    executor: Annotated[
        submitter.ReviewExecutor,
        fastapi.Depends(get_review_executor),
    ],
) -> submitter.BackgroundTaskSubmitter:
    """Provide a background-task job submitter.

    Args:
        background_tasks: Request-scoped FastAPI BackgroundTasks.
        executor: Executor that runs the agent.

    Returns:
        BackgroundTaskSubmitter instance.
    """
    return submitter.BackgroundTaskSubmitter(background_tasks, executor)


def get_review_job_service(
    job_repo: Annotated[
        repository.AbstractReviewJobRepository,
        fastapi.Depends(get_job_repository),
    ],
    content_source: Annotated[
        documents.DocumentContentSource,
        fastapi.Depends(get_document_content_source),
    ],
    job_submitter: Annotated[
        submitter.BackgroundTaskSubmitter,
        fastapi.Depends(get_job_submitter),
    ],
) -> service.ReviewJobService:
    """Provide the review job service.

    Args:
        job_repo: Job repository.
        content_source: Document content source.
        job_submitter: Job submitter.

    Returns:
        ReviewJobService instance.
    """
    return service.ReviewJobService(job_repo, content_source, job_submitter)
