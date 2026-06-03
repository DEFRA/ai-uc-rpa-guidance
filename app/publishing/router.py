"""FastAPI router for the publishing QA analysis endpoints."""

import fastapi

from app.publishing import api_schemas, service

router = fastapi.APIRouter(prefix="/publishing", tags=["publishing"])


@router.post("/analyse")
async def analyse_document(
    request: api_schemas.AnalyseRequest,
) -> api_schemas.AnalyseResponse:
    """Analyse a guidance document for quality issues.

    Accepts document text and returns structured analysis findings including
    identified issues, severity levels, and recommendations for remediation.

    Args:
        request: The analysis request containing document text.
        analysis_service: The analysis service, injected via FastAPI DI.

    Returns:
        Structured analysis response with findings and summary.

    Raises:
        HTTPException: On timeout (504) or other infrastructure errors (500).
    """

    return await service.analyse_document(request.document_text)
