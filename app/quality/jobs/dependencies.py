"""FastAPI dependency injection for the quality jobs domain."""

from typing import Annotated

import fastapi
import pymongo

from app.common import mongo
from app.guidance.prototype import dependencies as prototype_dependencies
from app.guidance.prototype import s3_repository
from app.quality.jobs import documents, repository, service, submitter


def get_job_repository(
    db: Annotated[
        pymongo.asynchronous.database.AsyncDatabase,
        fastapi.Depends(mongo.get_db),
    ],
) -> repository.AbstractQualityJobRepository:
    """Provide a quality job repository.

    Args:
        db: MongoDB database instance.

    Returns:
        MongoDB-backed job repository.
    """
    return repository.MongoQualityJobRepository(db)


def get_guide_content_source(
    s3_repo: Annotated[
        s3_repository.PrototypeGuideS3Repository,
        fastapi.Depends(prototype_dependencies.get_s3_repository),
    ],
) -> documents.GuideContentSource:
    """Provide a guide content source backed by the prototype guides.

    Args:
        s3_repo: The prototype guide S3 repository.

    Returns:
        PrototypeGuideContentSource adapter.
    """
    return documents.PrototypeGuideContentSource(s3_repo)


def get_check_executor(
    job_repo: Annotated[
        repository.AbstractQualityJobRepository,
        fastapi.Depends(get_job_repository),
    ],
) -> submitter.CheckExecutor:
    """Provide a check executor.

    Args:
        job_repo: Job repository for status transitions.

    Returns:
        CheckExecutor instance.
    """
    return submitter.CheckExecutor(job_repo)


def get_job_submitter(
    background_tasks: fastapi.BackgroundTasks,
    executor: Annotated[submitter.CheckExecutor, fastapi.Depends(get_check_executor)],
) -> submitter.BackgroundTaskSubmitter:
    """Provide a background-task job submitter.

    Args:
        background_tasks: Request-scoped FastAPI BackgroundTasks.
        executor: Executor that runs the checker.

    Returns:
        BackgroundTaskSubmitter instance.
    """
    return submitter.BackgroundTaskSubmitter(background_tasks, executor)


def get_quality_job_service(
    job_repo: Annotated[
        repository.AbstractQualityJobRepository,
        fastapi.Depends(get_job_repository),
    ],
    content_source: Annotated[
        documents.GuideContentSource,
        fastapi.Depends(get_guide_content_source),
    ],
    job_submitter: Annotated[
        submitter.BackgroundTaskSubmitter,
        fastapi.Depends(get_job_submitter),
    ],
) -> service.QualityJobService:
    """Provide the quality job service.

    Args:
        job_repo: Job repository.
        content_source: Guide content source.
        job_submitter: Job submitter.

    Returns:
        QualityJobService instance.
    """
    return service.QualityJobService(job_repo, content_source, job_submitter)
