"""Business logic service for guidance document summaries."""

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from app.guidance.documents import api_schemas as document_schemas
from app.guidance.documents import models as document_models
from app.guidance.documents import repository as document_repository
from app.guidance.documents import s3_repository
from app.guidance.summaries import api_schemas, models, repository
from app.guidance.summaries.agents import section_summariser, summariser
from app.infra.bedrock import llm

logger = logging.getLogger(__name__)

# Summaries are rebuilt across the whole corpus at once, and each document
# costs two model calls over its whole text. Bound the fan-out so a rebuild
# does not arrive at Bedrock as one burst.
MAX_CONCURRENT_SUMMARIES = 4


class SummaryError(Exception):
    """Raised when a document cannot be summarised."""


@dataclass
class _IndexedDocument:
    """What indexing one document produced."""

    summary: models.DocumentSummary
    sections: list[models.SectionSummary]


@dataclass
class _SectionIndex:
    """What the section pass produced: the entries, and the acronyms in them."""

    entries: list[models.SectionSummary]
    acronyms: list[models.AcronymEntry]


class SummaryService:
    """Service for building and reading the document and section index."""

    def __init__(
        self,
        documents: document_repository.GuidanceRepository,
        summaries: repository.SummaryRepository,
        sections: repository.SectionSummaryRepository,
        storage: s3_repository.AbstractGuidanceStorageRepository,
    ) -> None:
        """Initialize the service with its repositories.

        Args:
            documents: Repository holding the guidance documents themselves.
            summaries: Repository holding one summary per document.
            sections: Repository holding one entry per section.
            storage: Storage holding the parsed Markdown and the rendered
                index.
        """
        self.documents = documents
        self.summaries = summaries
        self.sections = sections
        self.storage = storage

    async def rebuild(
        self, document_ids: list[uuid.UUID]
    ) -> api_schemas.SummaryRebuildResponse:
        """Discard the whole index, then rebuild it from the given documents.

        The index is purged first, so afterwards it holds exactly what this
        rebuild produced: a document left out of the request is left out of the
        index, and so is one whose summary fails. Every document is attempted —
        one that cannot be summarised is reported as a failure rather than
        sinking the rest of the rebuild.

        Args:
            document_ids: The documents to summarise.

        Returns:
            What was purged, what was rebuilt, what failed, and how long it
            took: a rebuild is two model passes per document, so how long it
            ran is part of what the rebuild reports.
        """
        logger.info(
            "[Summary] Rebuilding summaries for %d document(s)", len(document_ids)
        )

        started = time.monotonic()

        purged = await self._purge()

        limit = asyncio.Semaphore(MAX_CONCURRENT_SUMMARIES)

        async def rebuild_one(
            document_id: uuid.UUID,
        ) -> _IndexedDocument | BaseException:
            async with limit:
                try:
                    return await self._rebuild_document(document_id)
                except Exception as exc:  # noqa: BLE001 - reported per document
                    logger.exception(
                        "[Summary] Failed to summarise document %s", document_id
                    )
                    return exc

        results = await asyncio.gather(*(rebuild_one(id_) for id_ in document_ids))

        items = [
            _to_response(result.summary, result.sections)
            for result in results
            if isinstance(result, _IndexedDocument)
        ]
        failures = [
            api_schemas.SummaryFailureResponse(
                document_id=str(document_id), error_message=str(result)
            )
            for document_id, result in zip(document_ids, results, strict=True)
            if isinstance(result, BaseException)
        ]

        duration = time.monotonic() - started

        logger.info(
            "[Summary] Rebuild complete in %.1fs: %d purged, %d summarised "
            "(%d section entries), %d failed",
            duration,
            purged,
            len(items),
            sum(len(item.sections) for item in items),
            len(failures),
        )

        return api_schemas.SummaryRebuildResponse(
            items=items,
            failures=failures,
            purged=purged,
            duration_seconds=round(duration, 1),
        )

    async def list_summaries(self) -> api_schemas.SummaryListResponse:
        """Return the whole index: each document, then its sections.

        Returns:
            The stored summaries, each carrying its sections in document order.
        """
        summaries = await self.summaries.list_summaries()
        sections = await self.sections.list_sections()

        by_document: dict[uuid.UUID, list[models.SectionSummary]] = defaultdict(list)
        for section in sections:
            by_document[section.document_id].append(section)

        return api_schemas.SummaryListResponse(
            items=[
                _to_response(summary, by_document[summary.document_id])
                for summary in summaries
            ]
        )

    async def _purge(self) -> int:
        """Discard every stored summary, record and artefact alike.

        Returns:
            How many summary records were discarded.
        """
        records = await self.summaries.delete_all_summaries()
        entries = await self.sections.delete_all_sections()
        objects = await self.storage.delete_summaries()

        logger.info(
            "[Summary] Purged %d summary record(s), %d section entr(y/ies) "
            "and %d object(s)",
            records,
            entries,
            objects,
        )

        return records

    async def _rebuild_document(self, document_id: uuid.UUID) -> _IndexedDocument:
        """Index one document: its own summary, and one entry per section.

        The two passes are one model call each over the same Markdown. The
        section pass is given the manifest's sections, so its entries can only
        name sections the document has.

        Args:
            document_id: The guidance document UUID.

        Returns:
            The stored summary and section entries.

        Raises:
            SummaryError: If the document is unknown or has not been parsed.
        """
        document = await self.documents.get_document(document_id)

        if document is None:
            msg = f"Document {document_id} not found"
            raise SummaryError(msg)

        if document.status is not document_models.ExtractionStatus.COMPLETE:
            msg = f"Document {document_id} has not been parsed ({document.status})"
            raise SummaryError(msg)

        markdown = await self.storage.download_content(document_id)
        manifest = await self._manifest_for(document_id)
        title = _title_from(document, manifest)

        logger.info("[Summary] Summarising %s (%d chars)", document_id, len(markdown))

        result = await summariser.summariser_agent.run(
            "Summarise the provided guidance document.",
            deps=models.SummaryDependencies(document_markdown=markdown),
            model=llm.claude_sonnet,
        )

        index = await self._rebuild_sections(title, document_id, markdown, manifest)

        summary = models.DocumentSummary(
            document_id=document_id,
            title=title,
            about=result.output.about,
            used_for=result.output.used_for,
            keywords=result.output.keywords,
            acronyms=index.acronyms,
            path="",
            start_path=models.start_path_for(document_id, _first_section(manifest)),
            model=llm.claude_sonnet.model_name,
            updated_at=datetime.now(tz=UTC),
        )

        summary.path = await self.storage.upload_summary(
            document_id, summary.render_markdown(index.entries)
        )

        await self.summaries.save_summary(summary)
        await self.sections.save_sections(index.entries)

        return _IndexedDocument(summary=summary, sections=index.entries)

    async def _rebuild_sections(
        self,
        title: str,
        document_id: uuid.UUID,
        markdown: str,
        manifest: document_schemas.DocumentManifestResponse | None,
    ) -> _SectionIndex:
        """Summarise each section of a document, in document order.

        The acronyms each section reports become the document's reverse index,
        and are folded into that section's own keywords: an acronym is the term
        a reader is most likely to search by, so it has to be in the list a
        keyword search reads.

        Args:
            title: The document's title, for the agent's context.
            document_id: The guidance document UUID.
            markdown: The document's parsed Markdown.
            manifest: The parse manifest naming the sections, if it has one.

        Returns:
            The section entries in manifest order, and the acronym index.
        """
        if manifest is None or not manifest.sections:
            return _SectionIndex(entries=[], acronyms=[])

        logger.info(
            "[Summary] Summarising %d section(s) of %s",
            len(manifest.sections),
            document_id,
        )

        result = await section_summariser.section_summariser_agent.run(
            "Summarise each section of the provided guidance document.",
            deps=models.SectionSummaryDependencies(
                document_title=title,
                document_markdown=markdown,
                sections=[
                    (section.number, section.heading) for section in manifest.sections
                ],
            ),
            model=llm.claude_sonnet,
        )

        summarised = {entry.number: entry for entry in result.output.sections}

        entries = [
            _section_entry(document_id, section, summarised[section.number], order)
            for order, section in enumerate(manifest.sections)
            if section.number in summarised
        ]

        return _SectionIndex(
            entries=entries,
            acronyms=models.build_acronym_index(
                [(entry.number, entry.acronyms) for entry in entries]
            ),
        )

    async def _manifest_for(
        self, document_id: uuid.UUID
    ) -> document_schemas.DocumentManifestResponse | None:
        """Return a document's parse manifest, or None if it has none.

        The manifest carries the document's own heading and the number its
        first section was given, neither of which the upload knows: a document
        uploaded without a title is otherwise only its filename.

        Args:
            document_id: The guidance document UUID.

        Returns:
            The manifest, or None if it could not be read.
        """
        try:
            raw = await self.storage.download_manifest(document_id)
            return document_schemas.DocumentManifestResponse.model_validate_json(raw)
        except Exception:  # noqa: BLE001 - a missing manifest is not a failure
            logger.info("[Summary] No manifest for document %s", document_id)
            return None


def _title_from(
    document: document_models.GuidanceDocument,
    manifest: document_schemas.DocumentManifestResponse | None,
) -> str:
    """Return the document's title as a reader would recognise it."""
    if manifest and manifest.title:
        return manifest.title

    return document.title or document.filename or str(document.id)


def _first_section(
    manifest: document_schemas.DocumentManifestResponse | None,
) -> str | None:
    """Return the number of the document's first section, if it has one."""
    if not manifest or not manifest.sections:
        return None

    return manifest.sections[0].number


def _section_entry(
    document_id: uuid.UUID,
    section: document_schemas.ManifestSectionNodeResponse,
    summarised: models.SectionSummaryOutput,
    order: int,
) -> models.SectionSummary:
    """Build one section's index entry from the manifest and the model's answer.

    The acronyms the section uses are kept whole — each with its expansion —
    and folded into its keywords as well, because an acronym is the term a
    reader is most likely to search by and the keywords are what a keyword
    search reads.
    """
    acronyms = [
        models.SectionAcronym(acronym=used.acronym, expansion=used.expansion)
        for used in summarised.acronyms
    ]

    return models.SectionSummary(
        document_id=document_id,
        number=section.number,
        heading=section.heading,
        level=section.level,
        summary=summarised.summary,
        keywords=_with_acronyms(summarised.keywords, acronyms),
        acronyms=acronyms,
        start_path=models.start_path_for(document_id, section.number),
        order=order,
    )


def _with_acronyms(
    keywords: list[str], acronyms: list[models.SectionAcronym]
) -> list[str]:
    """Return the keywords with every acronym present, in order, once each."""
    merged = dict.fromkeys(keywords)

    for used in acronyms:
        merged.setdefault(used.acronym)

    return list(merged)


def _to_response(
    summary: models.DocumentSummary, sections: list[models.SectionSummary]
) -> api_schemas.SummaryResponse:
    """Map a stored summary and its sections to their API shape."""
    return api_schemas.SummaryResponse(
        document_id=str(summary.document_id),
        title=summary.title,
        about=summary.about,
        used_for=summary.used_for,
        keywords=summary.keywords,
        acronyms=[
            api_schemas.AcronymResponse(
                acronym=entry.acronym,
                expansion=entry.expansion,
                sections=entry.sections,
            )
            for entry in summary.acronyms
        ],
        path=summary.path,
        start_path=summary.start_path,
        sections=[
            api_schemas.SectionSummaryResponse(
                number=section.number,
                heading=section.heading,
                level=section.level,
                summary=section.summary,
                keywords=section.keywords,
                acronyms=[
                    api_schemas.SectionAcronymResponse(
                        acronym=entry.acronym, expansion=entry.expansion
                    )
                    for entry in section.acronyms
                ],
                start_path=section.start_path,
            )
            for section in sections
        ],
        updated_at=summary.updated_at,
    )
