"""FastAPI router for the quality checks domain."""

import fastapi

from app.quality.jobs import router as jobs_router

router = fastapi.APIRouter(prefix="/quality-checks")
router.include_router(jobs_router.router)
