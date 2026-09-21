"""Business logic for searching the guidance index."""

import asyncio
import logging
import time

from pydantic_ai.models import bedrock

from app.guidance.documents import s3_repository
from app.guidance.search import api_schemas, index_text, models
from app.guidance.search.agents import answerer, assessor, ranker
from app.guidance.summaries import models as summary_models
from app.guidance.summaries import repository as summary_repository
from app.infra.bedrock import llm

# The index goes into the ranker's instructions, unchanged from one query to
# the next, so Bedrock can keep it cached rather than re-reading it every
# search. Measured, it buys no wall clock — ranking is spent generating, not
# reading — but it is most of the input of every search, and a rebuild changes
# the text so the cache falls away on its own. An hour outlives a session.
RANKER_SETTINGS = bedrock.BedrockModelSettings(bedrock_cache_instructions="1h")

logger = logging.getLogger(__name__)

# Each proposed section is read and judged by its own model call. They run
# together: the ranker returns at most ten results, so the whole check is one
# round trip rather than two waves of five.
MAX_CONCURRENT_ASSESSMENTS = ranker.MAX_RESULTS


class SearchService:
    """Service for answering an operator's query from the guidance index."""

    def __init__(
        self,
        summaries: summary_repository.SummaryRepository,
        sections: summary_repository.SectionSummaryRepository,
        storage: s3_repository.AbstractGuidanceStorageRepository,
    ) -> None:
        """Initialize the service with its repositories.

        Args:
            summaries: Repository holding one summary per document.
            sections: Repository holding one entry per section.
            storage: Storage holding the parsed section Markdown.
        """
        self.summaries = summaries
        self.sections = sections
        self.storage = storage

    async def search(self, query: str) -> api_schemas.SearchResponse:
        """Answer a query from the index, checking the sections it proposes.

        Three passes: the ranker reads the whole index and proposes entries;
        each proposed section is then read in full and judged against the
        query, all of them at once; the answerer writes the overview from what
        survived. A proposal the section itself does not bear out is dropped,
        so the operator is not sent to a page that only mentions their subject.

        Args:
            query: What the operator typed — keywords, a case type, an
                acronym, or a question.

        Returns:
            The overview where the results supported one, and the results.
        """
        started = time.monotonic()

        summaries, by_document = await self._load_index()
        loaded = time.monotonic()

        if not summaries:
            logger.info("[Search] Nothing is indexed; no results for %r", query)
            return self._response(query, None, [], 0, started)

        ranked = await self._rank(query, summaries, by_document)
        results = self._resolve(ranked, summaries, by_document)
        got_ranking = time.monotonic()

        results = await self._assess(query, results)
        assessed = time.monotonic()

        answer = await self._answer(query, results)

        logger.info(
            "[Search] %r answered in %.1fs with %d result(s) "
            "(index %.1fs, rank %.1fs, assess %.1fs x%d, answer %.1fs)",
            query,
            time.monotonic() - started,
            len(results),
            loaded - started,
            got_ranking - loaded,
            assessed - got_ranking,
            len(ranked),
            time.monotonic() - assessed,
        )

        return self._response(query, answer, results, len(summaries), started)

    async def _load_index(
        self,
    ) -> tuple[
        list[summary_models.DocumentSummary],
        dict[str, list[summary_models.SectionSummary]],
    ]:
        """Load the whole index: every document summary and section entry."""
        summaries = await self.summaries.list_summaries()
        sections = await self.sections.list_sections()

        by_document: dict[str, list[summary_models.SectionSummary]] = {}
        for section in sections:
            by_document.setdefault(str(section.document_id), []).append(section)

        return summaries, by_document

    async def _rank(
        self,
        query: str,
        summaries: list[summary_models.DocumentSummary],
        by_document: dict[str, list[summary_models.SectionSummary]],
    ) -> list[models.RankedResult]:
        """Ask the ranker which entries of the index answer the query."""
        entries: set[tuple[str, str | None]] = set()
        for summary in summaries:
            document_id = str(summary.document_id)
            entries.add((document_id, None))
            entries.update(
                (document_id, section.number)
                for section in by_document.get(document_id, [])
            )

        result = await ranker.ranker_agent.run(
            query,
            deps=models.RankDependencies(
                query=query,
                index=index_text.render(summaries, by_document),
                entries=entries,
            ),
            model=llm.claude_sonnet,
            model_settings=RANKER_SETTINGS,
        )

        return result.output.results

    def _resolve(
        self,
        ranked: list[models.RankedResult],
        summaries: list[summary_models.DocumentSummary],
        by_document: dict[str, list[summary_models.SectionSummary]],
    ) -> list[models.SearchResult]:
        """Turn the ranker's references back into the index entries they name."""
        documents = {str(summary.document_id): summary for summary in summaries}

        resolved = []
        for result in ranked:
            summary = documents[result.document_id]

            if result.section_number is None:
                resolved.append(
                    models.SearchResult(
                        document_id=summary.document_id,
                        document_title=summary.title,
                        section_number=None,
                        heading=summary.title,
                        summary=summary.about,
                        reason=summary.about,
                        start_path=summary.start_path,
                        checked=False,
                    )
                )
                continue

            section = next(
                entry
                for entry in by_document[result.document_id]
                if entry.number == result.section_number
            )
            resolved.append(
                models.SearchResult(
                    document_id=summary.document_id,
                    document_title=summary.title,
                    section_number=section.number,
                    heading=section.heading,
                    summary=section.summary,
                    reason=section.summary,
                    start_path=section.start_path,
                    checked=False,
                )
            )

        return resolved

    async def _assess(
        self, query: str, results: list[models.SearchResult]
    ) -> list[models.SearchResult]:
        """Read each proposed section in full and keep the ones that hold up.

        The ranker judged a section by its index entry. This reads the section
        itself. A document-level result has no deeper text to check — its
        evidence is the summary the ranker already read — so it is kept as it
        stands and reported as unchecked.
        """
        limit = asyncio.Semaphore(MAX_CONCURRENT_ASSESSMENTS)

        async def assess(result: models.SearchResult) -> models.SearchResult | None:
            if result.section_number is None:
                return result

            async with limit:
                try:
                    markdown = await self.storage.download_section(
                        result.document_id, result.section_number
                    )
                except Exception:  # noqa: BLE001 - an unreadable section stands
                    logger.info(
                        "[Search] Could not read %s/%s to check it",
                        result.document_id,
                        result.section_number,
                    )
                    return result

                verdict = await assessor.assessor_agent.run(
                    "Does this section answer the query?",
                    deps=models.AssessDependencies(
                        query=query,
                        document_title=result.document_title,
                        heading=result.heading,
                        section_markdown=markdown,
                    ),
                    model=llm.claude_sonnet,
                )

            if not verdict.output.relevant:
                logger.info(
                    "[Search] Dropping %s/%s: %s",
                    result.document_id,
                    result.section_number,
                    verdict.output.reason,
                )
                return None

            result.reason = verdict.output.reason
            result.checked = True
            return result

        assessed = await asyncio.gather(*(assess(result) for result in results))

        return [result for result in assessed if result is not None]

    async def _answer(
        self, query: str, results: list[models.SearchResult]
    ) -> models.SearchAnswer | None:
        """Write the overview from the results that survived, if they support one."""
        if not results:
            return None

        result = await answerer.answerer_agent.run(
            query,
            deps=models.AnswerDependencies(
                query=query,
                results=_render_results(results),
                cited={
                    (str(result.document_id), result.section_number)
                    for result in results
                },
            ),
            model=llm.claude_sonnet,
        )

        return result.output if result.output.answered else None

    def _response(
        self,
        query: str,
        answer: models.SearchAnswer | None,
        results: list[models.SearchResult],
        indexed: int,
        started: float,
    ) -> api_schemas.SearchResponse:
        """Map the search to its API shape."""
        by_reference = {
            (str(result.document_id), result.section_number): result
            for result in results
        }

        return api_schemas.SearchResponse(
            query=query,
            answer=(
                api_schemas.SearchAnswerResponse(
                    answer=answer.answer,
                    cited=[
                        _result_response(
                            by_reference[
                                (citation.document_id, citation.section_number)
                            ]
                        )
                        for citation in answer.cited
                    ],
                )
                if answer
                else None
            ),
            results=[_result_response(result) for result in results],
            indexed_documents=indexed,
            duration_seconds=round(time.monotonic() - started, 1),
        )


def _render_results(results: list[models.SearchResult]) -> str:
    """Render the results as the text the answerer is given."""
    return "\n\n".join(
        f"- document_id: {result.document_id}\n"
        f"  section_number: {result.section_number or 'null'}\n"
        f"  document: {result.document_title}\n"
        f"  section: {result.heading}\n"
        f"  index summary: {result.summary}\n"
        f"  what it gives the operator: {result.reason}"
        for result in results
    )


def _result_response(
    result: models.SearchResult,
) -> api_schemas.SearchResultResponse:
    """Map one result to its API shape."""
    return api_schemas.SearchResultResponse(
        document_id=str(result.document_id),
        document_title=result.document_title,
        section_number=result.section_number,
        heading=result.heading,
        summary=result.summary,
        reason=result.reason,
        start_path=result.start_path,
        checked=result.checked,
    )
