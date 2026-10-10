"""FastAPI dependency injection for the guidance search domain."""

from typing import Annotated

import fastapi

from app.guidance.prototype import dependencies as prototype_dependencies
from app.guidance.prototype import s3_repository
from app.guidance.search import service
from app.guidance.summaries import dependencies as summary_dependencies
from app.guidance.summaries import repository as summary_repository


def get_search_service(
    summaries: Annotated[
        summary_repository.SummaryRepository,
        fastapi.Depends(summary_dependencies.get_summary_repository),
    ],
    sections: Annotated[
        summary_repository.SectionSummaryRepository,
        fastapi.Depends(summary_dependencies.get_section_repository),
    ],
    guides: Annotated[
        s3_repository.PrototypeGuideS3Repository,
        fastapi.Depends(prototype_dependencies.get_s3_repository),
    ],
) -> service.SearchService:
    """Get the search service.

    Args:
        summaries: Repository for the document summaries.
        sections: Repository for the section entries.
        guides: Storage for the prototype guides the index names.

    Returns:
        Initialized SearchService.
    """
    return service.SearchService(summaries, sections, guides)
