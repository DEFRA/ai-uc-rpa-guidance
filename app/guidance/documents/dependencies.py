"""FastAPI dependency injection for the guidance domain."""

from typing import Annotated

import fastapi
import pymongo

from app import config
from app.common import mongo, s3
from app.guidance.documents import parser, repository, s3_repository, service

settings = config.get_config()


def get_guidance_repository(
    db: Annotated[
        pymongo.asynchronous.database.AsyncDatabase,
        fastapi.Depends(mongo.get_db),
    ],
) -> repository.GuidanceRepository:
    """Get the guidance repository.

    Args:
        db: MongoDB database instance.

    Returns:
        Initialized GuidanceRepository.
    """
    return repository.GuidanceRepository(db)


def get_s3_repository() -> s3_repository.GuidanceS3Repository:
    """Get the guidance S3 repository.

    Returns:
        Initialized GuidanceS3Repository.
    """
    return s3_repository.GuidanceS3Repository(
        s3.create_s3_client(), settings.guidance_s3_bucket
    )


def get_document_parser(
    s3_repo: Annotated[
        s3_repository.GuidanceS3Repository,
        fastapi.Depends(get_s3_repository),
    ],
) -> parser.DocumentParser:
    """Get the document parser.

    Returns:
        Initialized PipelineDocumentParser.
    """
    return parser.PipelineDocumentParser(s3_repo)


def get_guidance_service(
    repo: Annotated[
        repository.GuidanceRepository,
        fastapi.Depends(get_guidance_repository),
    ],
    doc_parser: Annotated[
        parser.DocumentParser,
        fastapi.Depends(get_document_parser),
    ],
) -> service.GuidanceService:
    """Get the guidance service.

    Args:
        repo: Guidance repository instance.
        doc_parser: Document parser instance.

    Returns:
        Initialized GuidanceService.
    """
    return service.GuidanceService(repo, doc_parser)
