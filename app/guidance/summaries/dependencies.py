"""FastAPI dependency injection for the guidance summaries domain."""

from typing import Annotated

import fastapi
import pymongo

from app.common import mongo
from app.guidance.documents import dependencies as document_dependencies
from app.guidance.documents import repository as document_repository
from app.guidance.documents import s3_repository
from app.guidance.summaries import repository, service


def get_summary_repository(
    db: Annotated[
        pymongo.asynchronous.database.AsyncDatabase,
        fastapi.Depends(mongo.get_db),
    ],
) -> repository.SummaryRepository:
    """Get the summary repository.

    Args:
        db: MongoDB database instance.

    Returns:
        Initialized SummaryRepository.
    """
    return repository.SummaryRepository(db)


def get_section_repository(
    db: Annotated[
        pymongo.asynchronous.database.AsyncDatabase,
        fastapi.Depends(mongo.get_db),
    ],
) -> repository.SectionSummaryRepository:
    """Get the section entry repository.

    Args:
        db: MongoDB database instance.

    Returns:
        Initialized SectionSummaryRepository.
    """
    return repository.SectionSummaryRepository(db)


def get_summary_service(
    documents: Annotated[
        document_repository.GuidanceRepository,
        fastapi.Depends(document_dependencies.get_guidance_repository),
    ],
    summaries: Annotated[
        repository.SummaryRepository,
        fastapi.Depends(get_summary_repository),
    ],
    sections: Annotated[
        repository.SectionSummaryRepository,
        fastapi.Depends(get_section_repository),
    ],
    storage: Annotated[
        s3_repository.GuidanceS3Repository,
        fastapi.Depends(document_dependencies.get_s3_repository),
    ],
) -> service.SummaryService:
    """Get the summary service.

    Args:
        documents: Repository for the guidance documents themselves.
        summaries: Repository for the summaries.
        sections: Repository for the section entries.
        storage: Storage for the parsed Markdown and the rendered index.

    Returns:
        Initialized SummaryService.
    """
    return service.SummaryService(documents, summaries, sections, storage)
