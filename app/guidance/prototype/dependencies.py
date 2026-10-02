"""FastAPI dependency injection for the prototype guides feature."""

from app import config
from app.common import s3
from app.guidance.prototype import s3_repository

settings = config.get_config()


def get_s3_repository() -> s3_repository.PrototypeGuideS3Repository:
    """Get the prototype guide S3 repository.

    Reuses the same guidance_s3_bucket as the CDP-uploader pipeline, just
    under the prototype_guides/ prefix instead of parsed_guidance/, so the
    two feature's data never collide.

    Returns:
        Initialized PrototypeGuideS3Repository.
    """
    return s3_repository.PrototypeGuideS3Repository(
        s3.create_s3_client(), settings.guidance_s3_bucket
    )
