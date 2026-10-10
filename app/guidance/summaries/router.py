"""FastAPI router for the guidance document summary endpoints."""

import logging
import uuid
from typing import Annotated

import fastapi

from app.guidance.summaries import (
    api_schemas,
    dependencies,
    models,
    service,
    worker,
)

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
    status_code=fastapi.status.HTTP_202_ACCEPTED,
    responses={
        fastapi.status.HTTP_202_ACCEPTED: {
            "description": "Rebuild queued; follow its progress at /rebuilds/{id}",
        },
        fastapi.status.HTTP_409_CONFLICT: {
            "description": "A rebuild is already queued or running",
        },
    },
)
async def rebuild_summaries(
    summary_service: Annotated[
        service.SummaryService,
        fastapi.Depends(dependencies.get_summary_service),
    ],
    rebuild_queue: Annotated[
        worker.RebuildQueue,
        fastapi.Depends(worker.get_rebuild_queue),
    ],
    payload: Annotated[api_schemas.RebuildRequest | None, fastapi.Body()] = None,
) -> api_schemas.RebuildResponse:
    """Queue a rebuild of the index from the prototype guides.

    A full rebuild (the default, and what no body asks for) discards the whole
    index first, so it ends up holding exactly what the rebuild produced. A
    partial one indexes only the guides whose content changed or that are new,
    and removes those no longer uploaded. Each guide indexed is summarised by
    the model, so a rebuild runs for minutes: this returns as soon as it is
    queued, and the rebuild's progress is read from
    GET /guidance/summaries/rebuilds/{rebuild_id}.

    Args:
        summary_service: The summary service, injected via FastAPI DI.
        rebuild_queue: The queue the rebuild worker serves.
        payload: Optionally, {"mode": "full" | "partial"}.

    Returns:
        The queued rebuild.

    Raises:
        HTTPException: 409 if a rebuild is already queued or running.
    """
    try:
        rebuild = await summary_service.request_rebuild(
            payload.mode if payload else models.RebuildMode.FULL
        )
    except service.RebuildInProgressError as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    await rebuild_queue.submit(uuid.UUID(rebuild.rebuild_id))

    return rebuild


@router.get(
    "/rebuilds/current",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "The rebuild that is queued or running",
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "No rebuild is queued or running",
        },
    },
)
async def get_current_rebuild(
    summary_service: Annotated[
        service.SummaryService,
        fastapi.Depends(dependencies.get_summary_service),
    ],
) -> api_schemas.RebuildResponse:
    """Return the rebuild that is queued or running, and its progress.

    Args:
        summary_service: The summary service, injected via FastAPI DI.

    Returns:
        The unfinished rebuild.

    Raises:
        HTTPException: 404 if no rebuild is queued or running.
    """
    rebuild = await summary_service.get_current_rebuild()

    if rebuild is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail="No rebuild is queued or running",
        )

    return rebuild


@router.post(
    "/rebuilds/{rebuild_id}/cancel",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "The rebuild, now cancelled",
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "No such rebuild",
        },
        fastapi.status.HTTP_409_CONFLICT: {
            "description": "The rebuild has already finished",
        },
    },
)
async def cancel_rebuild(
    rebuild_id: uuid.UUID,
    summary_service: Annotated[
        service.SummaryService,
        fastapi.Depends(dependencies.get_summary_service),
    ],
    rebuild_queue: Annotated[
        worker.RebuildQueue,
        fastapi.Depends(worker.get_rebuild_queue),
    ],
) -> api_schemas.RebuildResponse:
    """Cancel a queued or running rebuild.

    Returns once the rebuild has stopped and is recorded as cancelled, so a
    new rebuild can be requested straight away. What it had indexed so far is
    left in place until the next rebuild purges it.

    Args:
        rebuild_id: The rebuild to cancel.
        summary_service: The summary service, injected via FastAPI DI.
        rebuild_queue: The queue the rebuild worker serves.

    Returns:
        The cancelled rebuild.

    Raises:
        HTTPException: 404 if there is no such rebuild, 409 if it has
            already finished.
    """
    try:
        return await summary_service.cancel_rebuild(rebuild_id, rebuild_queue.cancel)
    except service.RebuildNotFoundError as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except service.RebuildNotActiveError as exc:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.get(
    "/rebuilds/{rebuild_id}",
    status_code=fastapi.status.HTTP_200_OK,
    responses={
        fastapi.status.HTTP_200_OK: {
            "description": "The rebuild and how far it has got",
        },
        fastapi.status.HTTP_404_NOT_FOUND: {
            "description": "No such rebuild",
        },
    },
)
async def get_rebuild(
    rebuild_id: uuid.UUID,
    summary_service: Annotated[
        service.SummaryService,
        fastapi.Depends(dependencies.get_summary_service),
    ],
) -> api_schemas.RebuildResponse:
    """Return a rebuild of the index and its progress.

    Args:
        rebuild_id: The rebuild's id.
        summary_service: The summary service, injected via FastAPI DI.

    Returns:
        The rebuild: its status, how many guides of how many are finished,
        the guide started last, and any that failed.

    Raises:
        HTTPException: 404 if there is no such rebuild.
    """
    rebuild = await summary_service.get_rebuild(rebuild_id)

    if rebuild is None:
        raise fastapi.HTTPException(
            status_code=fastapi.status.HTTP_404_NOT_FOUND,
            detail=f"No rebuild {rebuild_id}",
        )

    return rebuild
