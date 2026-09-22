"""FastAPI router for searching the guidance index."""

import logging
from typing import Annotated

import fastapi

from app.guidance.search import api_schemas, dependencies, service

router = fastapi.APIRouter(prefix="/guidance/search", tags=["search"])

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 500


@router.get(
    "/",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "The results, and the overview where there is one",
        },
    },
)
async def search_guidance(
    search_service: Annotated[
        service.SearchService,
        fastapi.Depends(dependencies.get_search_service),
    ],
    q: Annotated[
        str,
        fastapi.Query(
            min_length=1,
            max_length=MAX_QUERY_LENGTH,
            description="What the operator typed",
        ),
    ],
) -> api_schemas.SearchResponse:
    """Search the guidance index on an operator's behalf.

    The whole index is given to the ranking agent, the sections it proposes
    are read and checked against the query, and the overview is written from
    what survived. It is a long request: several model calls, of which the
    checks run together.

    Args:
        search_service: The search service, injected via FastAPI DI.
        q: The query — keywords, a case type, an acronym, or a question.

    Returns:
        The overview and the ranked results.
    """
    return await search_service.search(q)
