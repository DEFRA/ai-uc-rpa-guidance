"""FastAPI dependency injection for the guidance summaries domain."""

from typing import Annotated

import fastapi
import pymongo

from app.common import mongo
from app.guidance.documents import dependencies as document_dependencies
from app.guidance.documents import s3_repository
from app.guidance.prototype import dependencies as prototype_dependencies
from app.guidance.prototype import s3_repository as prototype_s3_repository
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


def get_rebuild_repository(
    db: Annotated[
        pymongo.asynchronous.database.AsyncDatabase,
        fastapi.Depends(mongo.get_db),
    ],
) -> repository.RebuildRepository:
    """Get the rebuild repository.

    Args:
        db: MongoDB database instance.

    Returns:
        Initialized RebuildRepository.
    """
    return repository.RebuildRepository(db)


def get_summary_service(
    guides: Annotated[
        prototype_s3_repository.PrototypeGuideS3Repository,
        fastapi.Depends(prototype_dependencies.get_s3_repository),
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
    rebuilds: Annotated[
        repository.RebuildRepository,
        fastapi.Depends(get_rebuild_repository),
    ],
) -> service.SummaryService:
    """Get the summary service.

    Args:
        guides: Storage for the prototype guides the index is built from.
        summaries: Repository for the summaries.
        sections: Repository for the section entries.
        storage: Storage for the rendered summary Markdown.
        rebuilds: Repository for the rebuilds and their progress.

    Returns:
        Initialized SummaryService.
    """
    return service.SummaryService(guides, summaries, sections, storage, rebuilds)


def summary_service_for(
    db: pymongo.asynchronous.database.AsyncDatabase,
) -> service.SummaryService:
    """Build the summary service outside a request, as the rebuild worker does.

    Args:
        db: MongoDB database instance.

    Returns:
        Initialized SummaryService.
    """
    return get_summary_service(
        guides=prototype_dependencies.get_s3_repository(),
        summaries=get_summary_repository(db),
        sections=get_section_repository(db),
        storage=document_dependencies.get_s3_repository(),
        rebuilds=get_rebuild_repository(db),
    )
