"""FastAPI router for the review domain."""

import fastapi

from app.review.jobs import router as jobs_router

router = fastapi.APIRouter(prefix="/review")
router.include_router(jobs_router.router)
