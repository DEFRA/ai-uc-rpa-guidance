"""FastAPI router for the guidance document summary endpoints."""

import logging
from typing import Annotated

import fastapi

from app.guidance.summaries import api_schemas, dependencies, service

router = fastapi.APIRouter(prefix="/guidance/summaries", tags=["summaries"])

logger = logging.getLogger(__name__)


@router.get(
    "/",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Every stored document summary",
        },
    },
)
async def list_summaries(
    summary_service: Annotated[
        service.SummaryService,
        fastapi.Depends(dependencies.get_summary_service),
    ],
) -> api_schemas.SummaryListResponse:
    """Return the summary held for each document that has one.

    Args:
        summary_service: The summary service, injected via FastAPI DI.

    Returns:
        The stored summaries, most recently rebuilt first.
    """
    return await summary_service.list_summaries()


@router.post(
    "/rebuild",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "Index rebuilt, with what was purged and what failed",
        },
    },
)
async def rebuild_summaries(
    payload: api_schemas.SummaryRebuildRequest,
    summary_service: Annotated[
        service.SummaryService,
        fastapi.Depends(dependencies.get_summary_service),
    ],
) -> api_schemas.SummaryRebuildResponse:
    """Rebuild the index from the requested documents.

    The whole index is discarded first, so it ends up holding exactly what this
    rebuild produced. Each document is summarised by the model from its parsed
    Markdown, so this is a long request: it returns when every requested
    summary has been built or has failed.

    Args:
        payload: The documents to summarise.
        summary_service: The summary service, injected via FastAPI DI.

    Returns:
        What was purged, what was rebuilt, and what failed.
    """
    return await summary_service.rebuild(list(payload.document_ids))
