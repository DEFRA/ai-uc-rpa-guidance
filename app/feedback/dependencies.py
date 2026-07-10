"""FastAPI dependency injection for the feedback domain."""

from typing import Annotated

import fastapi
import pymongo

from app.common import mongo
from app.critique.jobs import dependencies as critique_dependencies
from app.critique.jobs import repository as critique_repository
from app.feedback import models, repository, service, sources
from app.publishing.jobs import dependencies as publishing_dependencies
from app.publishing.jobs import repository as publishing_repository


def get_feedback_repository(
    db: Annotated[
        pymongo.asynchronous.database.AsyncDatabase,
        fastapi.Depends(mongo.get_db),
    ],
) -> repository.AbstractFeedbackRepository:
    """Provide a feedback repository.

    Args:
        db: MongoDB database instance.

    Returns:
        MongoDB-backed feedback repository.
    """
    return repository.MongoFeedbackRepository(db)


def get_publishing_finding_source(
    job_repo: Annotated[
        publishing_repository.AbstractPublishingJobRepository,
        fastapi.Depends(publishing_dependencies.get_job_repository),
    ],
) -> sources.PublishingFindingSource:
    """Provide a FindingSource adapter for publishing (checker) jobs.

    Args:
        job_repo: Publishing job repository.

    Returns:
        PublishingFindingSource adapter.
    """
    return sources.PublishingFindingSource(job_repo)


def get_critique_finding_source(
    job_repo: Annotated[
        critique_repository.AbstractCritiqueJobRepository,
        fastapi.Depends(critique_dependencies.get_job_repository),
    ],
) -> sources.CritiqueFindingSource:
    """Provide a FindingSource adapter for critique (critic) jobs.

    Args:
        job_repo: Critique job repository.

    Returns:
        CritiqueFindingSource adapter.
    """
    return sources.CritiqueFindingSource(job_repo)


def get_feedback_service(
    feedback_repo: Annotated[
        repository.AbstractFeedbackRepository,
        fastapi.Depends(get_feedback_repository),
    ],
    publishing_source: Annotated[
        sources.PublishingFindingSource,
        fastapi.Depends(get_publishing_finding_source),
    ],
    critique_source: Annotated[
        sources.CritiqueFindingSource,
        fastapi.Depends(get_critique_finding_source),
    ],
) -> service.FeedbackService:
    """Provide the feedback service.

    Args:
        feedback_repo: Feedback repository.
        publishing_source: FindingSource adapter for publishing jobs.
        critique_source: FindingSource adapter for critique jobs.

    Returns:
        FeedbackService instance.
    """
    finding_sources: dict[models.AgentName, sources.FindingSource] = {
        models.AgentName.CHECKER: publishing_source,
        models.AgentName.CRITIC: critique_source,
    }
    return service.FeedbackService(feedback_repo, finding_sources)
