from logging import getLogger
from typing import Any

import fastapi
import httpx
import pymongo

from app import config as app_config
from app.common import http_client, mongo

router = fastapi.APIRouter(prefix="/example")
logger = getLogger(__name__)


@router.get("/test")
async def root() -> dict[str, bool]:
    logger.info("TEST ENDPOINT")
    return {"ok": True}


@router.get("/db")
async def db_query(
    db: pymongo.asynchronous.database.AsyncDatabase = fastapi.Depends(mongo.get_db),
) -> dict[str, Any]:
    await db.example.insert_one({"foo": "bar"})
    data = await db.example.find_one({}, {"_id": 0})
    return {"ok": data}


@router.get("/http")
async def http_query(
    client: httpx.AsyncClient = fastapi.Depends(http_client.create_async_client),
) -> dict[str, int]:
    endpoint = app_config.get_config().aws_endpoint_url or "http://localstack:4566"
    resp = await client.get(f"{endpoint}/health")
    return {"ok": resp.status_code}
