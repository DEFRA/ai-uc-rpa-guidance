"""Business logic service for the guidance index: guide and section summaries.

The index is built from the prototype guides: the latest version of every
guide in the prototype manifest. A rebuild is long — two model calls per guide
— so it is requested, queued, and run by a worker in the background (see
worker.py), recording its progress as it goes.

A full rebuild discards the index and indexes every guide. A partial one keeps
it: a guide whose content.md still has the hash its summary was built from is
skipped, the rest are indexed, and guides no longer uploaded are removed. A
guide's summary is saved last, after its sections, so a summary carrying a
hash marks a guide whose indexing finished.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import botocore.exceptions

from app.guidance.documents import s3_repository
from app.guidance.prototype import manifest as prototype_manifest
from app.guidance.prototype import s3_repository as prototype_s3_repository
from app.guidance.prototype import sections as prototype_sections
from app.guidance.summaries import api_schemas, models, repository
from app.guidance.summaries.agents import section_summariser, summariser
from app.infra.bedrock import llm

logger = logging.getLogger(__name__)

# Each guide costs two model calls over its whole text. Bound the fan-out so a
# rebuild does not arrive at Bedrock as one burst: at five at once, section
# passes started hanging until the client's read timeout.
MAX_CONCURRENT_SUMMARIES = 3

INTERRUPTED = "Interrupted by restart"

_UNFINISHED = (models.RebuildStatus.QUEUED, models.RebuildStatus.RUNNING)


class RebuildInProgressError(Exception):
    """Raised when a rebuild is requested while another is unfinished."""


class RebuildNotFoundError(Exception):
    """Raised when there is no rebuild with the given id."""


class RebuildNotActiveError(Exception):
    """Raised when a rebuild that has already finished is cancelled."""


@dataclass(frozen=True)
class _Guide:
    """A guide to index: the latest version of one prototype guide."""

    document_id: uuid.UUID
    title: str
    version_id: str


@dataclass
class _SectionIndex:
    """What the section pass produced: the entries, and the acronyms in them."""

    entries: list[models.SectionSummary]
    acronyms: list[models.AcronymEntry]


class SummaryService:
    """Service for building and reading the guide and section index."""

    def __init__(
        self,
        guides: prototype_s3_repository.PrototypeGuideS3Repository,
        summaries: repository.SummaryRepository,
        sections: repository.SectionSummaryRepository,
        storage: s3_repository.AbstractGuidanceStorageRepository,
        rebuilds: repository.RebuildRepository,
    ) -> None:
        """Initialize the service with its repositories.

        Args:
            guides: Storage holding the prototype guides and their manifest.
            summaries: Repository holding one summary per guide.
            sections: Repository holding one entry per section.
            storage: Storage holding the rendered summary Markdown.
            rebuilds: Repository holding each rebuild and its progress.
        """
        self.guides = guides
        self.summaries = summaries
        self.sections = sections
        self.storage = storage
        self.rebuilds = rebuilds

    async def request_rebuild(
        self, mode: models.RebuildMode = models.RebuildMode.FULL
    ) -> api_schemas.RebuildResponse:
        """Record a new rebuild, queued for the worker to run.

        Args:
            mode: Whether to rebuild the whole index or only what changed.

        Returns:
            The queued rebuild.

        Raises:
            RebuildInProgressError: If a rebuild is already queued or running.
        """
        if await self.rebuilds.find_active() is not None:
            msg = "A rebuild of the index is already queued or running"
            raise RebuildInProgressError(msg)

        rebuild = await self.rebuilds.create_rebuild(
            models.Rebuild(rebuild_id=uuid.uuid4(), mode=mode)
        )

        logger.info("[Summary] %s rebuild %s queued", mode, rebuild.rebuild_id)

        return _to_rebuild_response(rebuild)

    async def get_rebuild(
        self, rebuild_id: uuid.UUID
    ) -> api_schemas.RebuildResponse | None:
        """Return a rebuild and how far it has got.

        Args:
            rebuild_id: The rebuild's id.

        Returns:
            The rebuild, or None if there is none with this id.
        """
        rebuild = await self.rebuilds.get_rebuild(rebuild_id)
        return _to_rebuild_response(rebuild) if rebuild else None

    async def get_current_rebuild(self) -> api_schemas.RebuildResponse | None:
        """Return the rebuild that is queued or running, if there is one.

        Returns:
            The unfinished rebuild, or None.
        """
        rebuild = await self.rebuilds.find_active()
        return _to_rebuild_response(rebuild) if rebuild else None

    async def cancel_rebuild(
        self,
        rebuild_id: uuid.UUID,
        stop: Callable[[uuid.UUID], Awaitable[None]],
    ) -> api_schemas.RebuildResponse:
        """Stop a queued or running rebuild, and record it as cancelled.

        It is stopped first, so that once this returns nothing more is
        written for it and a new rebuild can be requested at once. Whatever it
        had indexed is left in place: the next rebuild purges it.

        Args:
            rebuild_id: The rebuild to cancel.
            stop: What stops it: skips it if queued, cancels it if running.

        Returns:
            The cancelled rebuild.

        Raises:
            RebuildNotFoundError: If there is no such rebuild.
            RebuildNotActiveError: If it has already finished.
        """
        rebuild = await self.rebuilds.get_rebuild(rebuild_id)

        if rebuild is None:
            msg = f"No rebuild {rebuild_id}"
            raise RebuildNotFoundError(msg)

        if rebuild.status not in _UNFINISHED:
            msg = f"Rebuild {rebuild_id} is already {rebuild.status}"
            raise RebuildNotActiveError(msg)

        await stop(rebuild_id)

        duration = (datetime.now(tz=UTC) - _aware(rebuild.created_at)).total_seconds()
        if not await self.rebuilds.cancel(
            rebuild_id, duration_seconds=round(max(duration, 0), 1)
        ):
            msg = f"Rebuild {rebuild_id} finished before it could be cancelled"
            raise RebuildNotActiveError(msg)

        logger.info("[Summary] Rebuild %s cancelled", rebuild_id)

        cancelled = await self.rebuilds.get_rebuild(rebuild_id)
        return _to_rebuild_response(cancelled or rebuild)

    async def fail_interrupted_rebuilds(self) -> None:
        """Fail every rebuild left unfinished, as at startup.

        The queue is held in memory, so a rebuild queued or running when the
        service stopped will never be run: it is failed rather than left
        looking busy, which would also refuse every new rebuild.
        """
        failed = await self.rebuilds.fail_unfinished(INTERRUPTED)

        if failed:
            logger.warning("[Summary] Failed %d interrupted rebuild(s)", failed)

    async def run_rebuild(self, rebuild_id: uuid.UUID) -> None:
        """Rebuild the index from the prototype guides, fully or partially.

        A full rebuild purges the index first, so afterwards it holds exactly
        what this rebuild produced. A partial one indexes only the guides that
        changed or are new, and removes those no longer uploaded; the guides
        it skips count as finished from the start. Every guide indexed is
        attempted — one that cannot be indexed is recorded as a failure rather
        than sinking the rest. Progress is recorded as each guide starts and
        finishes. The rebuild as a whole fails only if the guides cannot be
        listed.

        Args:
            rebuild_id: The queued rebuild to run.
        """
        started = time.monotonic()

        try:
            rebuild = await self.rebuilds.get_rebuild(rebuild_id)
            mode = rebuild.mode if rebuild else models.RebuildMode.FULL
            guides = await self._latest_guides()

            if mode is models.RebuildMode.FULL:
                purged = await self._purge()
                to_index: list[tuple[_Guide, bytes | None]] = [
                    (guide, None) for guide in guides
                ]
            else:
                purged, to_index = await self._changed_guides(guides)

            await self.rebuilds.begin(
                rebuild_id,
                total=len(guides),
                purged=purged,
                skipped=len(guides) - len(to_index),
            )

            logger.info(
                "[Summary] %s rebuild %s: indexing %d of %d guide(s)",
                mode,
                rebuild_id,
                len(to_index),
                len(guides),
            )

            limit = asyncio.Semaphore(MAX_CONCURRENT_SUMMARIES)

            async def index_one(guide: _Guide, content: bytes | None) -> None:
                async with limit:
                    await self.rebuilds.start_guide(rebuild_id, guide.title)
                    guide_started = time.monotonic()
                    failure = await self._index_or_fail(guide, content)
                    logger.info(
                        "[Summary] Indexed %s in %.1fs%s",
                        guide.title,
                        time.monotonic() - guide_started,
                        " (failed)" if failure else "",
                    )
                    await self.rebuilds.finish_guide(rebuild_id, failure)

            await asyncio.gather(
                *(index_one(guide, content) for guide, content in to_index)
            )
        except Exception as exc:  # noqa: BLE001 - recorded on the rebuild
            logger.exception("[Summary] Rebuild %s failed", rebuild_id)
            await self.rebuilds.fail(rebuild_id, str(exc))
            return

        duration = round(time.monotonic() - started, 1)
        await self.rebuilds.complete(rebuild_id, duration_seconds=duration)

        logger.info("[Summary] Rebuild %s complete in %.1fs", rebuild_id, duration)

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

    async def _changed_guides(
        self, guides: list[_Guide]
    ) -> tuple[int, list[tuple[_Guide, bytes | None]]]:
        """Find the guides a partial rebuild must index, and drop the departed.

        Each guide's content is read once here and handed on to be indexed. A
        guide whose content cannot be read is indexed anyway, so that it is
        reported as a failure rather than silently skipped.

        Args:
            guides: The latest version of every guide in the manifest.

        Returns:
            How many guides' entries were removed, and the guides to index,
            each with its content where it was read.
        """
        stored = {
            summary.document_id: summary
            for summary in await self.summaries.list_summaries()
        }

        present = {guide.document_id for guide in guides}
        departed = [document_id for document_id in stored if document_id not in present]
        for document_id in departed:
            await self._remove(document_id)

        contents = await asyncio.gather(
            *(self._read_or_none(guide) for guide in guides)
        )

        changed = []
        for guide, content in zip(guides, contents, strict=True):
            summary = stored.get(guide.document_id)
            if (
                content is None
                or summary is None
                or summary.content_sha256 != _sha256(content)
            ):
                changed.append((guide, content))

        return len(departed), changed

    async def _read_or_none(self, guide: _Guide) -> bytes | None:
        """Read a guide's content.md, or None if it cannot be read."""
        try:
            return await self.guides.download_content(
                str(guide.document_id), guide.version_id
            )
        except Exception:  # noqa: BLE001 - indexing it will report the failure
            logger.warning("[Summary] Could not read guide %s", guide.document_id)
            return None

    async def _remove(self, document_id: uuid.UUID) -> None:
        """Remove a guide's entries from the index: summary, sections, Markdown."""
        await self.summaries.delete_summary(document_id)
        await self.sections.delete_sections(document_id)
        await self.storage.delete_summary(document_id)

        logger.info("[Summary] Removed guide %s, no longer uploaded", document_id)

    async def _latest_guides(self) -> list[_Guide]:
        """Return the latest version of every prototype guide.

        Returns:
            The guides to index, in manifest order: none if no guides have
            been uploaded.
        """
        try:
            raw = await self.guides.download_manifest()
        except botocore.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] == "NoSuchKey":
                return []
            raise

        return [
            _Guide(
                document_id=uuid.UUID(latest.document_id),
                title=latest.title or latest.document_id,
                version_id=latest.version_id,
            )
            for latest in prototype_manifest.latest_versions(json.loads(raw))
        ]

    async def _index_or_fail(
        self, guide: _Guide, content: bytes | None
    ) -> models.RebuildFailure | None:
        """Index one guide, returning why if it could not be.

        Args:
            guide: The guide to index.
            content: Its content.md, if already read.

        Returns:
            None if the guide was indexed, else the failure.
        """
        try:
            await self._index_guide(guide, content)
        except Exception as exc:  # noqa: BLE001 - reported per guide
            logger.exception("[Summary] Failed to index guide %s", guide.document_id)
            return models.RebuildFailure(
                document_id=str(guide.document_id),
                title=guide.title,
                error_message=str(exc),
            )
        return None

    async def _index_guide(self, guide: _Guide, content: bytes | None) -> None:
        """Index one guide: its own summary, and one entry per section.

        The two passes are one model call each over the same Markdown. The
        section pass is given the guide's sections, so its entries can only
        name sections the guide has. The guide's old sections are replaced
        first and its summary saved last, carrying the hash of the content it
        was built from: a guide with a summary is a guide fully indexed.

        Args:
            guide: The guide to index.
            content: Its content.md, if already read; else it is read here.
        """
        raw = content
        if raw is None:
            raw = await self.guides.download_content(
                str(guide.document_id), guide.version_id
            )
        markdown = raw.decode()

        logger.info(
            "[Summary] Summarising %s (%d chars)", guide.document_id, len(markdown)
        )

        result = await summariser.summariser_agent.run(
            "Summarise the provided guidance document.",
            deps=models.SummaryDependencies(document_markdown=markdown),
            model=llm.claude_sonnet,
        )

        index = await self._rebuild_sections(
            guide.title,
            guide.document_id,
            markdown,
            prototype_sections.split(markdown),
        )

        summary = models.DocumentSummary(
            document_id=guide.document_id,
            title=guide.title,
            about=result.output.about,
            used_for=result.output.used_for,
            keywords=result.output.keywords,
            acronyms=index.acronyms,
            path="",
            start_path=models.content_path_for(guide.document_id),
            model=llm.claude_sonnet.model_name,
            content_sha256=_sha256(raw),
            version_id=guide.version_id,
            updated_at=datetime.now(tz=UTC),
        )

        await self.sections.delete_sections(guide.document_id)
        await self.sections.save_sections(index.entries)

        summary.path = await self.storage.upload_summary(
            guide.document_id, summary.render_markdown(index.entries)
        )

        await self.summaries.save_summary(summary)

    async def _rebuild_sections(
        self,
        title: str,
        document_id: uuid.UUID,
        markdown: str,
        guide_sections: list[prototype_sections.Section],
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
            guide_sections: The guide's sections, in document order.

        Returns:
            The section entries in document order, and the acronym index.
        """
        if not guide_sections:
            return _SectionIndex(entries=[], acronyms=[])

        logger.info(
            "[Summary] Summarising %d section(s) of %s",
            len(guide_sections),
            document_id,
        )

        result = await section_summariser.section_summariser_agent.run(
            "Summarise each section of the provided guidance document.",
            deps=models.SectionSummaryDependencies(
                document_title=title,
                document_markdown=markdown,
                sections=[
                    (section.number, section.heading) for section in guide_sections
                ],
            ),
            model=llm.claude_sonnet,
        )

        summarised = {entry.number: entry for entry in result.output.sections}

        entries = [
            _section_entry(document_id, section, summarised[section.number], order)
            for order, section in enumerate(guide_sections)
            if section.number in summarised
        ]

        return _SectionIndex(
            entries=entries,
            acronyms=models.build_acronym_index(
                [(entry.number, entry.acronyms) for entry in entries]
            ),
        )


def _section_entry(
    document_id: uuid.UUID,
    section: prototype_sections.Section,
    summarised: models.SectionSummaryOutput,
    order: int,
) -> models.SectionSummary:
    """Build one section's index entry from the guide and the model's answer.

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
        start_path=models.section_path_for(document_id, section.number),
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
        content_sha256=summary.content_sha256,
        version_id=summary.version_id,
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


def _to_rebuild_response(rebuild: models.Rebuild) -> api_schemas.RebuildResponse:
    """Map a rebuild to its API shape."""
    return api_schemas.RebuildResponse(
        rebuild_id=str(rebuild.rebuild_id),
        mode=rebuild.mode.value,
        status=rebuild.status.value,
        total=rebuild.total,
        completed=rebuild.completed,
        current_title=rebuild.current_title,
        failures=[
            api_schemas.RebuildFailureResponse(
                document_id=failure.document_id,
                title=failure.title,
                error_message=failure.error_message,
            )
            for failure in rebuild.failures
        ],
        purged=rebuild.purged,
        skipped=rebuild.skipped,
        duration_seconds=rebuild.duration_seconds,
        error_message=rebuild.error_message,
    )


def _aware(moment: datetime) -> datetime:
    """Return the moment as UTC-aware: Mongo hands datetimes back naive."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _sha256(content: bytes) -> str:
    """Return the hex SHA-256 of content exactly as read."""
    return hashlib.sha256(content).hexdigest()
