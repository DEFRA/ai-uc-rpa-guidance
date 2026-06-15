"""FastAPI router for the publishing domain."""

import fastapi

from app.publishing.jobs import router as jobs_router

router = fastapi.APIRouter(prefix="/publishing")
router.include_router(jobs_router.router)
