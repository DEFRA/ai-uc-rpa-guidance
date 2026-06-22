"""FastAPI router for the guidance language critique endpoint."""

import fastapi

from app.critique import api_schemas, service

router = fastapi.APIRouter(prefix="/critique", tags=["critique"])


@router.post("/analyse")
async def critique_document(
    request: api_schemas.CritiqueRequest,
) -> api_schemas.CritiqueResponse:
    """Review a guidance document against GDS and DEFRA style standards.

    Runs a critic/writer loop that reports conformance and divergence per
    standard and returns a revised document with text-level improvements.

    Args:
        request: The critique request containing markdown document text.

    Returns:
        Per-standard reports, the revised document, loop history, invariant
        warnings, and token usage.
    """
    return await service.critique_document(
        request.document_text,
        max_iterations=request.max_iterations,
        revise=request.revise,
    )
