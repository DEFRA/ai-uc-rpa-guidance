"""Document parser protocol and pipeline implementation."""

import dataclasses
import io
import json
import logging
from dataclasses import dataclass
from typing import Protocol

import docx

from app.guidance.documents import models, s3_repository
from app.guidance.pipeline import models as pipeline_models
from app.guidance.pipeline import service as pipeline_service
from app.guidance.pipeline.renderers import markdown as markdown_renderer

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Result returned by a document parser."""

    status: models.ExtractionStatus
    content: str | None = None
    title: str | None = None
    error_message: str | None = None


class DocumentParser(Protocol):
    """Protocol for parsing guidance documents."""

    async def parse(self, document: models.GuidanceDocument) -> ParseResult:
        """Parse a guidance document and return the result."""
        ...


def _build_manifest(
    document_id: str, tree: pipeline_models.DocumentTree
) -> pipeline_models.DocumentManifest:
    nodes = [
        pipeline_models.ManifestSectionNode(
            number=s.number,
            heading=s.heading,
            level=s.level,
            parent=s.number.rsplit(".", 1)[0] if "." in s.number else None,
            children=[c.number for c in s.children],
        )
        for s in tree.sections
    ]
    return pipeline_models.DocumentManifest(
        document_id=document_id,
        title=tree.title,
        sections=nodes,
    )


class PipelineDocumentParser:
    """Parses a .docx from S3 via the pipeline and stores rendered content back to S3."""

    def __init__(
        self,
        s3_repo: s3_repository.AbstractGuidanceStorageRepository,
    ) -> None:
        self.s3_repo = s3_repo

    async def parse(self, document: models.GuidanceDocument) -> ParseResult:
        """Download, parse, and upload a guidance document.

        Returns a ParseResult describing the outcome. Never raises — failures
        are captured in the result's status and error_message fields.
        """
        try:
            if not document.path:
                msg = f"Document {document.id} has no path set; cannot download source file"
                raise ValueError(msg)

            if not document.path.startswith("s3://"):
                msg = f"Document {document.id} has invalid path {document.path}; must start with s3://"
                raise ValueError(msg)

            _, key = document.path.replace("s3://", "").split("/", 1)

            logger.info("Starting parse of document %s from s3://%s", document.id, key)

            docx_bytes = await self.s3_repo.download_docx(key)

            with io.BytesIO(docx_bytes) as docx_stream:
                doc = docx.Document(docx_stream)

                tree = pipeline_service.parse_doc(doc, title=document.title or None)

                inferred_title: str | None = None
                if not document.title and tree.title:
                    inferred_title = tree.title

                rendered_markdown = markdown_renderer.to_markdown(tree)

                await self.s3_repo.upload_content(document.id, rendered_markdown)

                manifest = _build_manifest(str(document.id), tree)
                await self.s3_repo.upload_manifest(
                    document.id, json.dumps(dataclasses.asdict(manifest))
                )

                for section in tree.sections:
                    section_md = markdown_renderer.section_to_markdown(section)
                    await self.s3_repo.upload_section(
                        document.id, section.number, section_md
                    )

                logger.info(
                    "Document %s parsed and stored successfully (%d sections)",
                    document.id,
                    len(tree.sections),
                )

                return ParseResult(
                    status=models.ExtractionStatus.COMPLETE,
                    content=rendered_markdown,
                    title=inferred_title,
                )

        except Exception as exc:
            logger.exception("Failed to parse document %s: %s", document.id, exc)
            return ParseResult(
                status=models.ExtractionStatus.FAILED,
                error_message=str(exc),
            )
