"""Document parser protocol and pipeline implementation."""

import dataclasses
import io
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

import docx

from app.guidance.documents import models, s3_repository
from app.guidance.pipeline import models as pipeline_models
from app.guidance.pipeline import service as pipeline_service
from app.guidance.pipeline.renderers import markdown as markdown_renderer

_CONTENT_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}


def _ext_to_content_type(ext: str) -> str:
    return _CONTENT_TYPES.get(ext.lower(), "application/octet-stream")


def _collect_images(
    tree: pipeline_models.DocumentTree,
) -> list[tuple[str, pipeline_models.ImageNode]]:
    """Return (section_number, ImageNode) pairs from the tree in document order."""
    images: list[tuple[str, pipeline_models.ImageNode]] = []

    def _walk(section: pipeline_models.SectionNode) -> None:
        for node in section.content:
            if isinstance(node, pipeline_models.ImageNode):
                images.append((section.number, node))
        for child in section.children:
            _walk(child)

    for section in tree.children:
        _walk(section)

    return images


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

                await self._upload_images(document.id, tree)

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

    async def _upload_images(
        self, document_id: uuid.UUID, tree: pipeline_models.DocumentTree
    ) -> None:
        """Upload each ImageNode in the tree to S3 and set its rel_path to the API endpoint."""
        section_counters: dict[str, int] = {}
        for section_number, node in _collect_images(tree):
            section_counters[section_number] = (
                section_counters.get(section_number, 0) + 1
            )
            idx = section_counters[section_number]
            filename = f"{section_number}_img_{idx}{node.ext}"
            await self.s3_repo.upload_image(
                document_id,
                filename,
                node.data,
                _ext_to_content_type(node.ext),
            )
            node.rel_path = f"/guidance/documents/{document_id}/images/{filename}"
