"""Tests for the guidance index (summary) service."""

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import botocore.exceptions
import pytest

from app.guidance.documents import s3_repository
from app.guidance.prototype import s3_repository as prototype_s3_repository
from app.guidance.summaries import api_schemas, models, repository, service

DOCUMENT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
REBUILD_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
V1_ID = "aaaaaaaa-0000-0000-0000-000000000001"
V2_ID = "bbbbbbbb-0000-0000-0000-000000000002"


def _guide_entry(
    document_id: uuid.UUID = DOCUMENT_ID, title: str = "The Real Title"
) -> dict[str, Any]:
    return {
        "documentId": str(document_id),
        "title": title,
        "latestVersion": 2,
        "versions": [
            {"version": 1, "versionId": V1_ID},
            {"version": 2, "versionId": V2_ID},
        ],
    }


def _manifest(*guides: dict[str, Any]) -> bytes:
    entries = guides or (_guide_entry(),)
    return json.dumps({f"guide-{n}": guide for n, guide in enumerate(entries)}).encode()


def _content(*numbers: str, title: str = "The Real Title") -> bytes:
    sections = "".join(
        f"## {number} Heading {number}\n\nText of {number}.\n\n" for number in numbers
    )
    return f"# {title}\n\n{sections}".encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _no_such_key() -> botocore.exceptions.ClientError:
    return botocore.exceptions.ClientError(
        error_response={"Error": {"Code": "NoSuchKey", "Message": "Not there"}},
        operation_name="GetObject",
    )


def _make_summary(
    document_id: uuid.UUID = DOCUMENT_ID, title: str = "A Guide"
) -> models.DocumentSummary:
    return models.DocumentSummary(
        document_id=document_id,
        title=title,
        about="What it is about.",
        used_for="What it is used for.",
        path=f"s3://bucket/parsed_guidance/{document_id}/summary.md",
        keywords=["ROCR"],
        acronyms=[],
        start_path=f"/prototype/guides/{document_id}/content",
        model="anthropic.claude-sonnet-4-6",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _section_result(
    *numbers: str, acronyms: dict[str, list[models.AcronymOutput]] | None = None
) -> Mock:
    result = Mock()
    result.output = models.SectionSummariesOutput(
        sections=[
            models.SectionSummaryOutput(
                number=number,
                summary=f"What section {number} covers.",
                keywords=[f"term-{number}"],
                acronyms=(acronyms or {}).get(number, []),
            )
            for number in numbers
        ]
    )
    return result


def _make_section(
    document_id: uuid.UUID, number: str, order: int
) -> models.SectionSummary:
    return models.SectionSummary(
        document_id=document_id,
        number=number,
        heading=f"Heading {number}",
        level=1,
        summary=f"What section {number} covers.",
        keywords=[],
        acronyms=[],
        start_path=f"/prototype/guides/{document_id}/sections/{number}",
        order=order,
    )


def _agent_result(
    about: str = "What it is about.",
    used_for: str = "What it is used for.",
    keywords: list[str] | None = None,
) -> Mock:
    result = Mock()
    result.output = models.SummaryOutput(
        about=about,
        used_for=used_for,
        keywords=["ROCR"] if keywords is None else keywords,
    )
    return result


def _agents(*numbers: str) -> Any:
    """Patch the section pass, which every rebuild runs after the document pass."""
    return patch(
        "app.guidance.summaries.service.section_summariser.section_summariser_agent.run",
        new_callable=AsyncMock,
        return_value=_section_result(*(numbers or ("1", "2"))),
    )


def _summariser(**kwargs: Any) -> Any:
    """Patch the document pass."""
    return patch(
        "app.guidance.summaries.service.summariser.summariser_agent.run",
        new_callable=AsyncMock,
        **({"return_value": _agent_result()} | kwargs),
    )


async def _rebuild(
    summary_service: service.SummaryService,
) -> api_schemas.SummaryListResponse:
    """Run a rebuild, then read back the index it wrote."""
    await summary_service.run_rebuild(REBUILD_ID)
    return await summary_service.list_summaries()


@pytest.fixture
def guides() -> AsyncMock:
    repo = AsyncMock(spec=prototype_s3_repository.PrototypeGuideS3Repository)
    repo.download_manifest.return_value = _manifest()
    repo.download_content.return_value = _content("1", "2")
    return repo


@pytest.fixture
def summaries() -> AsyncMock:
    saved: list[models.DocumentSummary] = []
    repo = AsyncMock(spec=repository.SummaryRepository)
    repo.save_summary.side_effect = lambda summary: saved.append(summary) or summary
    repo.list_summaries.side_effect = lambda: list(saved)
    repo.delete_all_summaries.return_value = 0
    return repo


@pytest.fixture
def sections() -> AsyncMock:
    saved: list[models.SectionSummary] = []
    repo = AsyncMock(spec=repository.SectionSummaryRepository)
    repo.save_sections.side_effect = lambda entries: saved.extend(entries) or entries
    repo.list_sections.side_effect = lambda: list(saved)
    repo.delete_all_sections.return_value = 0
    return repo


@pytest.fixture
def storage() -> AsyncMock:
    repo = AsyncMock(spec=s3_repository.AbstractGuidanceStorageRepository)
    repo.upload_summary.return_value = (
        f"s3://bucket/parsed_guidance/{DOCUMENT_ID}/summary.md"
    )
    repo.delete_summaries.return_value = 0
    return repo


@pytest.fixture
def rebuilds() -> AsyncMock:
    repo = AsyncMock(spec=repository.RebuildRepository)
    repo.find_active.return_value = None
    repo.get_rebuild.return_value = models.Rebuild(rebuild_id=REBUILD_ID)
    repo.create_rebuild.side_effect = lambda rebuild: rebuild
    return repo


@pytest.fixture
def summary_service(
    guides: AsyncMock,
    summaries: AsyncMock,
    sections: AsyncMock,
    storage: AsyncMock,
    rebuilds: AsyncMock,
) -> service.SummaryService:
    return service.SummaryService(guides, summaries, sections, storage, rebuilds)


class TestRebuild:
    async def test_summarises_the_latest_version_of_the_guide(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        with _agents(), _summariser() as run:
            response = await _rebuild(summary_service)

        assert len(response.items) == 1
        assert response.items[0].about == "What it is about."
        assert response.items[0].used_for == "What it is used for."
        guides.download_content.assert_awaited_once_with(str(DOCUMENT_ID), V2_ID)
        assert run.await_args.kwargs["deps"].document_markdown == (
            guides.download_content.return_value.decode()
        )

    async def test_indexes_every_guide_in_the_manifest(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        guides.download_manifest.return_value = _manifest(
            _guide_entry(), _guide_entry(OTHER_ID, "Another Guide")
        )

        with _agents(), _summariser():
            response = await _rebuild(summary_service)

        assert {item.title for item in response.items} == {
            "The Real Title",
            "Another Guide",
        }

    async def test_stores_the_rendered_markdown_under_its_frontmatter(
        self,
        summary_service: service.SummaryService,
        guides: AsyncMock,
        storage: AsyncMock,
        summaries: AsyncMock,
    ) -> None:
        guides.download_content.return_value = _content("1")

        with _agents("1"), _summariser():
            await _rebuild(summary_service)

        document_id, markdown = storage.upload_summary.await_args.args
        saved = summaries.save_summary.await_args.args[0]
        assert document_id == DOCUMENT_ID
        assert markdown == (
            "---\n"
            f'document_id: "{DOCUMENT_ID}"\n'
            f'version_id: "{V2_ID}"\n'
            f'content_sha256: "{_sha256(_content("1"))}"\n'
            f'model: "{saved.model}"\n'
            f'updated_at: "{saved.updated_at.isoformat()}"\n'
            "---\n\n"
            "# The Real Title\n\n"
            f"[Open this document](/prototype/guides/{DOCUMENT_ID}/content)\n\n"
            "## What this document is about\n\n"
            "What it is about.\n\n"
            "## What it is used for\n\n"
            "What it is used for.\n\n"
            "## Terms\n\n"
            "ROCR\n\n"
            "## Sections\n\n"
            "### 1 Heading 1\n\n"
            f"[Open this section](/prototype/guides/{DOCUMENT_ID}/sections/1)\n\n"
            "What section 1 covers.\n\n"
            "Terms: term-1\n"
        )

    async def test_the_stored_markdown_spells_out_a_sections_acronyms(
        self,
        summary_service: service.SummaryService,
        guides: AsyncMock,
        storage: AsyncMock,
    ) -> None:
        guides.download_content.return_value = _content("1")
        section_pass = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                acronyms={
                    "1": [
                        models.AcronymOutput(
                            acronym="SDA", expansion="Severely Disadvantaged Area"
                        ),
                        models.AcronymOutput(acronym="IAPA", expansion=None),
                    ]
                },
            ),
        )

        with section_pass, _summariser():
            await _rebuild(summary_service)

        _, markdown = storage.upload_summary.await_args.args
        assert "Acronyms: SDA (Severely Disadvantaged Area), IAPA" in markdown

    async def test_records_the_summary_against_the_guide(
        self, summary_service: service.SummaryService
    ) -> None:
        with _agents(), _summariser():
            response = await _rebuild(summary_service)

        assert response.items[0].document_id == str(DOCUMENT_ID)
        assert response.items[0].path == (
            f"s3://bucket/parsed_guidance/{DOCUMENT_ID}/summary.md"
        )

    async def test_links_the_summary_to_the_whole_guide(
        self, summary_service: service.SummaryService
    ) -> None:
        with _agents(), _summariser():
            response = await _rebuild(summary_service)

        assert response.items[0].start_path == (
            f"/prototype/guides/{DOCUMENT_ID}/content"
        )

    async def test_titles_the_summary_from_the_manifest(
        self, summary_service: service.SummaryService
    ) -> None:
        with _agents(), _summariser():
            response = await _rebuild(summary_service)

        assert response.items[0].title == "The Real Title"

    async def test_titles_an_untitled_guide_by_its_id(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        guides.download_manifest.return_value = _manifest(_guide_entry(title=""))

        with _agents(), _summariser():
            response = await _rebuild(summary_service)

        assert response.items[0].title == str(DOCUMENT_ID)

    async def test_one_failure_does_not_sink_the_rest(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        guides.download_manifest.return_value = _manifest(
            _guide_entry(), _guide_entry(OTHER_ID, "Another Guide")
        )
        guides.download_content.side_effect = lambda document_id, _: (
            _content("1") if document_id == str(OTHER_ID) else b"\xff not text"
        )

        with _agents("1"), _summariser():
            response = await _rebuild(summary_service)

        assert [item.document_id for item in response.items] == [str(OTHER_ID)]

    async def test_indexes_nothing_when_no_guides_are_uploaded(
        self,
        summary_service: service.SummaryService,
        guides: AsyncMock,
        rebuilds: AsyncMock,
    ) -> None:
        guides.download_manifest.side_effect = _no_such_key()

        with _summariser() as run:
            response = await _rebuild(summary_service)

        assert response.items == []
        run.assert_not_awaited()
        rebuilds.begin.assert_awaited_once_with(
            REBUILD_ID, total=0, purged=0, skipped=0
        )
        rebuilds.complete.assert_awaited_once()


class TestSaveOrder:
    async def test_replaces_the_guides_sections_before_saving_its_summary(
        self,
        summary_service: service.SummaryService,
        summaries: AsyncMock,
        sections: AsyncMock,
        storage: AsyncMock,
    ) -> None:
        order: list[str] = []
        sections.delete_sections.side_effect = lambda document_id: (
            order.append(f"delete sections {document_id}") or 0
        )
        sections.save_sections.side_effect = lambda entries: (
            order.append("save sections") or entries
        )
        storage.upload_summary.side_effect = lambda *_: (
            order.append("upload summary.md") or "s3://bucket/summary.md"
        )
        summaries.save_summary.side_effect = lambda summary: (
            order.append("save summary") or summary
        )

        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        assert order == [
            f"delete sections {DOCUMENT_ID}",
            "save sections",
            "upload summary.md",
            "save summary",
        ]

    async def test_records_the_content_hash_and_version_it_was_built_from(
        self,
        summary_service: service.SummaryService,
        guides: AsyncMock,
        summaries: AsyncMock,
    ) -> None:
        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        saved = summaries.save_summary.await_args.args[0]
        assert saved.content_sha256 == _sha256(guides.download_content.return_value)
        assert saved.version_id == V2_ID

    async def test_lists_the_hash_and_version_with_the_summary(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        with _agents(), _summariser():
            response = await _rebuild(summary_service)

        assert response.items[0].content_sha256 == _sha256(
            guides.download_content.return_value
        )
        assert response.items[0].version_id == V2_ID


def _stored(
    document_id: uuid.UUID = DOCUMENT_ID, content: bytes | None = None
) -> models.DocumentSummary:
    summary = _make_summary(document_id)
    summary.content_sha256 = _sha256(content) if content is not None else None
    return summary


class TestPartialRebuild:
    @pytest.fixture(autouse=True)
    def _partial(self, rebuilds: AsyncMock) -> None:
        rebuilds.get_rebuild.return_value = models.Rebuild(
            rebuild_id=REBUILD_ID, mode=models.RebuildMode.PARTIAL
        )

    async def test_skips_a_guide_whose_content_is_unchanged(
        self,
        summary_service: service.SummaryService,
        guides: AsyncMock,
        summaries: AsyncMock,
        rebuilds: AsyncMock,
    ) -> None:
        summaries.list_summaries.side_effect = lambda: [
            _stored(content=guides.download_content.return_value)
        ]

        with _agents(), _summariser() as run:
            await summary_service.run_rebuild(REBUILD_ID)

        run.assert_not_awaited()
        rebuilds.start_guide.assert_not_awaited()
        rebuilds.begin.assert_awaited_once_with(
            REBUILD_ID, total=1, purged=0, skipped=1
        )
        rebuilds.complete.assert_awaited_once()

    async def test_indexes_a_guide_whose_content_has_changed(
        self,
        summary_service: service.SummaryService,
        summaries: AsyncMock,
    ) -> None:
        summaries.list_summaries.side_effect = lambda: [
            _stored(content=b"an older version")
        ]

        with _agents(), _summariser() as run:
            await summary_service.run_rebuild(REBUILD_ID)

        run.assert_awaited_once()

    async def test_indexes_a_guide_that_was_never_hashed(
        self, summary_service: service.SummaryService, summaries: AsyncMock
    ) -> None:
        summaries.list_summaries.side_effect = lambda: [_stored()]

        with _agents(), _summariser() as run:
            await summary_service.run_rebuild(REBUILD_ID)

        run.assert_awaited_once()

    async def test_counts_every_guide_but_fast_forwards_past_the_skipped(
        self,
        summary_service: service.SummaryService,
        guides: AsyncMock,
        summaries: AsyncMock,
        rebuilds: AsyncMock,
    ) -> None:
        guides.download_manifest.return_value = _manifest(
            _guide_entry(), _guide_entry(OTHER_ID, "Another Guide")
        )
        unchanged = _content("1", title="The Real Title")
        guides.download_content.side_effect = lambda document_id, _: (
            unchanged if document_id == str(DOCUMENT_ID) else _content("1", "2")
        )
        summaries.list_summaries.side_effect = lambda: [_stored(content=unchanged)]

        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        rebuilds.begin.assert_awaited_once_with(
            REBUILD_ID, total=2, purged=0, skipped=1
        )
        rebuilds.start_guide.assert_awaited_once_with(REBUILD_ID, "Another Guide")

    async def test_reads_each_guides_content_once(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        guides.download_content.assert_awaited_once_with(str(DOCUMENT_ID), V2_ID)

    async def test_keeps_the_rest_of_the_index(
        self,
        summary_service: service.SummaryService,
        summaries: AsyncMock,
        sections: AsyncMock,
        storage: AsyncMock,
    ) -> None:
        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        summaries.delete_all_summaries.assert_not_awaited()
        sections.delete_all_sections.assert_not_awaited()
        storage.delete_summaries.assert_not_awaited()

    async def test_removes_a_guide_no_longer_uploaded(
        self,
        summary_service: service.SummaryService,
        summaries: AsyncMock,
        sections: AsyncMock,
        storage: AsyncMock,
        rebuilds: AsyncMock,
    ) -> None:
        summaries.list_summaries.side_effect = lambda: [_stored(OTHER_ID)]

        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        summaries.delete_summary.assert_awaited_once_with(OTHER_ID)
        sections.delete_sections.assert_any_await(OTHER_ID)
        storage.delete_summary.assert_awaited_once_with(OTHER_ID)
        rebuilds.begin.assert_awaited_once_with(
            REBUILD_ID, total=1, purged=1, skipped=0
        )


class TestProgress:
    async def test_begins_with_how_many_guides_and_what_was_purged(
        self,
        summary_service: service.SummaryService,
        guides: AsyncMock,
        summaries: AsyncMock,
        rebuilds: AsyncMock,
    ) -> None:
        guides.download_manifest.return_value = _manifest(
            _guide_entry(), _guide_entry(OTHER_ID, "Another Guide")
        )
        summaries.delete_all_summaries.return_value = 7

        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        rebuilds.begin.assert_awaited_once_with(
            REBUILD_ID, total=2, purged=7, skipped=0
        )

    async def test_records_each_guide_as_it_starts(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        rebuilds.start_guide.assert_awaited_once_with(REBUILD_ID, "The Real Title")

    async def test_counts_an_indexed_guide_as_finished(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        rebuilds.finish_guide.assert_awaited_once_with(REBUILD_ID, None)

    async def test_counts_a_failed_guide_as_finished_and_says_why(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        with _agents(), _summariser(side_effect=RuntimeError("Bedrock said no")):
            await summary_service.run_rebuild(REBUILD_ID)

        rebuilds.finish_guide.assert_awaited_once_with(
            REBUILD_ID,
            models.RebuildFailure(
                document_id=str(DOCUMENT_ID),
                title="The Real Title",
                error_message="Bedrock said no",
            ),
        )
        rebuilds.complete.assert_awaited_once()

    async def test_completes_with_how_long_it_took(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        assert rebuilds.complete.await_args.args[0] == REBUILD_ID
        assert rebuilds.complete.await_args.kwargs["duration_seconds"] >= 0

    async def test_fails_the_rebuild_when_the_guides_cannot_be_read(
        self,
        summary_service: service.SummaryService,
        guides: AsyncMock,
        rebuilds: AsyncMock,
    ) -> None:
        guides.download_manifest.side_effect = RuntimeError("S3 is down")

        await summary_service.run_rebuild(REBUILD_ID)

        rebuilds.fail.assert_awaited_once_with(REBUILD_ID, "S3 is down")
        rebuilds.complete.assert_not_awaited()

    async def test_indexes_at_most_three_guides_at_once(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        guides.download_manifest.return_value = _manifest(
            *(_guide_entry(uuid.uuid4(), f"Guide {n}") for n in range(8))
        )
        running = 0
        most = 0

        async def summarise(*_: Any, **__: Any) -> Mock:
            nonlocal running, most
            running += 1
            most = max(most, running)
            await asyncio.sleep(0.01)
            running -= 1
            return _agent_result()

        with _agents(), _summariser(side_effect=summarise):
            await summary_service.run_rebuild(REBUILD_ID)

        assert most == 3


class TestRequestRebuild:
    async def test_queues_a_partial_rebuild_when_asked(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        response = await summary_service.request_rebuild(models.RebuildMode.PARTIAL)

        created = rebuilds.create_rebuild.await_args.args[0]
        assert created.mode is models.RebuildMode.PARTIAL
        assert response.mode == "partial"

    async def test_queues_a_new_rebuild(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        response = await summary_service.request_rebuild()

        created = rebuilds.create_rebuild.await_args.args[0]
        assert response.rebuild_id == str(created.rebuild_id)
        assert response.status == "queued"

    async def test_refuses_while_another_is_unfinished(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        rebuilds.find_active.return_value = models.Rebuild(rebuild_id=REBUILD_ID)

        with pytest.raises(service.RebuildInProgressError):
            await summary_service.request_rebuild()

        rebuilds.create_rebuild.assert_not_awaited()


class TestGetRebuild:
    async def test_reports_its_progress(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        rebuilds.get_rebuild.return_value = models.Rebuild(
            rebuild_id=REBUILD_ID,
            status=models.RebuildStatus.RUNNING,
            total=17,
            completed=4,
            current_title="A guide",
            failures=[
                models.RebuildFailure(
                    document_id="abc", title="Bad guide", error_message="No"
                )
            ],
            purged=3,
        )

        response = await summary_service.get_rebuild(REBUILD_ID)

        assert response is not None
        assert response.model_dump() == {
            "rebuild_id": str(REBUILD_ID),
            "mode": "full",
            "status": "running",
            "total": 17,
            "completed": 4,
            "current_title": "A guide",
            "failures": [
                {"document_id": "abc", "title": "Bad guide", "error_message": "No"}
            ],
            "purged": 3,
            "skipped": 0,
            "duration_seconds": None,
            "error_message": None,
        }

    async def test_an_unknown_rebuild_is_none(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        rebuilds.get_rebuild.return_value = None

        assert await summary_service.get_rebuild(REBUILD_ID) is None


class TestCurrentRebuild:
    async def test_reports_the_unfinished_rebuild(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        rebuilds.find_active.return_value = models.Rebuild(rebuild_id=REBUILD_ID)

        response = await summary_service.get_current_rebuild()

        assert response is not None
        assert response.rebuild_id == str(REBUILD_ID)

    async def test_is_none_when_nothing_is_unfinished(
        self, summary_service: service.SummaryService
    ) -> None:
        assert await summary_service.get_current_rebuild() is None


class TestCancelRebuild:
    async def test_stops_the_rebuild_then_records_it_cancelled(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        order: list[str] = []
        unfinished = models.Rebuild(
            rebuild_id=REBUILD_ID, status=models.RebuildStatus.RUNNING
        )
        cancelled = models.Rebuild(
            rebuild_id=REBUILD_ID,
            status=models.RebuildStatus.CANCELLED,
            duration_seconds=2.0,
        )
        rebuilds.get_rebuild.side_effect = [unfinished, cancelled]
        rebuilds.cancel.side_effect = lambda *_, **__: order.append("record") or True

        async def stop(rebuild_id: uuid.UUID) -> None:
            assert rebuild_id == REBUILD_ID
            order.append("stop")

        response = await summary_service.cancel_rebuild(REBUILD_ID, stop)

        assert order == ["stop", "record"]
        assert rebuilds.cancel.await_args.kwargs["duration_seconds"] >= 0
        assert response.status == "cancelled"

    async def test_refuses_an_unknown_rebuild(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        rebuilds.get_rebuild.return_value = None
        stop = AsyncMock()

        with pytest.raises(service.RebuildNotFoundError):
            await summary_service.cancel_rebuild(REBUILD_ID, stop)

        stop.assert_not_awaited()

    async def test_refuses_a_finished_rebuild(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        rebuilds.get_rebuild.return_value = models.Rebuild(
            rebuild_id=REBUILD_ID, status=models.RebuildStatus.COMPLETE
        )
        stop = AsyncMock()

        with pytest.raises(service.RebuildNotActiveError):
            await summary_service.cancel_rebuild(REBUILD_ID, stop)

        stop.assert_not_awaited()

    async def test_refuses_a_rebuild_that_finished_while_stopping(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        rebuilds.get_rebuild.return_value = models.Rebuild(rebuild_id=REBUILD_ID)
        rebuilds.cancel.return_value = False

        with pytest.raises(service.RebuildNotActiveError):
            await summary_service.cancel_rebuild(REBUILD_ID, AsyncMock())


class TestFailInterruptedRebuilds:
    async def test_fails_every_unfinished_rebuild(
        self, summary_service: service.SummaryService, rebuilds: AsyncMock
    ) -> None:
        await summary_service.fail_interrupted_rebuilds()

        rebuilds.fail_unfinished.assert_awaited_once_with("Interrupted by restart")


class TestSectionEntries:
    async def test_indexes_every_section_of_the_guide(
        self, summary_service: service.SummaryService
    ) -> None:
        with _agents("1", "2"), _summariser() as run:
            response = await _rebuild(summary_service)

        entries = response.items[0].sections
        assert [entry.number for entry in entries] == ["1", "2"]
        assert entries[0].heading == "Heading 1"
        assert entries[0].level == 1
        assert entries[0].summary == "What section 1 covers."
        assert entries[0].keywords == ["term-1"]
        assert run.await_count == 1

    async def test_gives_the_section_pass_the_guides_sections(
        self, summary_service: service.SummaryService
    ) -> None:
        with _agents("1", "2") as section_run, _summariser():
            await _rebuild(summary_service)

        assert section_run.await_args.kwargs["deps"].sections == [
            ("1", "Heading 1"),
            ("2", "Heading 2"),
        ]

    async def test_links_each_section_to_its_own_endpoint(
        self, summary_service: service.SummaryService
    ) -> None:
        with _agents("1", "2"), _summariser():
            response = await _rebuild(summary_service)

        assert [entry.start_path for entry in response.items[0].sections] == [
            f"/prototype/guides/{DOCUMENT_ID}/sections/1",
            f"/prototype/guides/{DOCUMENT_ID}/sections/2",
        ]

    async def test_keeps_the_guides_order_whatever_order_the_model_answers_in(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        guides.download_content.return_value = _content("1", "1.9", "1.10")

        with _agents("1.10", "1", "1.9"), _summariser():
            response = await _rebuild(summary_service)

        assert [entry.number for entry in response.items[0].sections] == [
            "1",
            "1.9",
            "1.10",
        ]

    async def test_leaves_out_a_section_the_model_did_not_summarise(
        self, summary_service: service.SummaryService
    ) -> None:
        with _agents("1"), _summariser():
            response = await _rebuild(summary_service)

        assert [entry.number for entry in response.items[0].sections] == ["1"]

    async def test_indexes_no_sections_for_a_guide_without_any(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        guides.download_content.return_value = _content()

        with (
            _summariser(),
            patch(
                "app.guidance.summaries.service.section_summariser."
                "section_summariser_agent.run",
                new_callable=AsyncMock,
            ) as section_run,
        ):
            response = await _rebuild(summary_service)

        assert response.items[0].sections == []
        section_run.assert_not_awaited()


class TestTermsAndAcronyms:
    async def test_records_the_terms_the_document_would_be_searched_by(
        self, summary_service: service.SummaryService
    ) -> None:
        with (
            _agents(),
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(keywords=["LULC", "Land Use or Land Cover"]),
            ),
        ):
            response = await _rebuild(summary_service)

        assert response.items[0].keywords == ["LULC", "Land Use or Land Cover"]

    async def test_indexes_the_acronyms_its_sections_use(
        self, summary_service: service.SummaryService
    ) -> None:
        sections = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                "2",
                acronyms={
                    "1": [models.AcronymOutput(acronym="SDA", expansion=None)],
                    "2": [
                        models.AcronymOutput(
                            acronym="SDA", expansion="Severely Disadvantaged Area"
                        )
                    ],
                },
            ),
        )

        with (
            sections,
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await _rebuild(summary_service)

        index = response.items[0].acronyms
        assert len(index) == 1
        assert index[0].acronym == "SDA"
        assert index[0].expansion == "Severely Disadvantaged Area"
        assert index[0].sections == ["1", "2"]

    async def test_a_sections_acronyms_are_among_its_own_terms(
        self, summary_service: service.SummaryService
    ) -> None:
        sections = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                acronyms={
                    "1": [
                        models.AcronymOutput(acronym="SSSI", expansion=None),
                        # Already a keyword: it must not be listed twice.
                        models.AcronymOutput(acronym="term-1", expansion=None),
                    ]
                },
            ),
        )

        with (
            sections,
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await _rebuild(summary_service)

        assert response.items[0].sections[0].keywords == ["term-1", "SSSI"]

    async def test_a_section_keeps_its_acronyms_with_their_expansions(
        self, summary_service: service.SummaryService
    ) -> None:
        sections = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                "2",
                acronyms={
                    "1": [
                        models.AcronymOutput(
                            acronym="SDA", expansion="Severely Disadvantaged Area"
                        )
                    ],
                    "2": [models.AcronymOutput(acronym="IAPA", expansion=None)],
                },
            ),
        )

        with (
            sections,
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await _rebuild(summary_service)

        first, second = response.items[0].sections
        assert [entry.acronym for entry in first.acronyms] == ["SDA"]
        assert first.acronyms[0].expansion == "Severely Disadvantaged Area"
        assert [entry.acronym for entry in second.acronyms] == ["IAPA"]
        assert second.acronyms[0].expansion is None

    async def test_the_index_names_only_sections_that_carry_the_acronym(
        self, summary_service: service.SummaryService
    ) -> None:
        sections = patch(
            "app.guidance.summaries.service.section_summariser."
            "section_summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_section_result(
                "1",
                "2",
                acronyms={"2": [models.AcronymOutput(acronym="SSSI", expansion=None)]},
            ),
        )

        with (
            sections,
            patch(
                "app.guidance.summaries.service.summariser.summariser_agent.run",
                new_callable=AsyncMock,
                return_value=_agent_result(),
            ),
        ):
            response = await _rebuild(summary_service)

        document = response.items[0]
        assert document.acronyms[0].sections == ["2"]
        assert document.sections[0].acronyms == []
        assert [entry.acronym for entry in document.sections[1].acronyms] == ["SSSI"]

    async def test_indexes_no_acronyms_for_a_document_without_sections(
        self, summary_service: service.SummaryService, guides: AsyncMock
    ) -> None:
        guides.download_content.return_value = _content()

        with patch(
            "app.guidance.summaries.service.summariser.summariser_agent.run",
            new_callable=AsyncMock,
            return_value=_agent_result(),
        ):
            response = await _rebuild(summary_service)

        assert response.items[0].acronyms == []


class TestPurge:
    async def test_discards_the_whole_index_before_rebuilding(
        self, summary_service: service.SummaryService, summaries: AsyncMock
    ) -> None:
        order: list[str] = []
        summaries.delete_all_summaries.side_effect = lambda: order.append("purge") or 0
        summaries.save_summary.side_effect = lambda summary: (
            order.append("save"),
            summary,
        )[1]

        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        assert order == ["purge", "save"]

    async def test_discards_the_section_entries_too(
        self, summary_service: service.SummaryService, sections: AsyncMock
    ) -> None:
        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        sections.delete_all_sections.assert_awaited_once()

    async def test_discards_the_stored_markdown_too(
        self, summary_service: service.SummaryService, storage: AsyncMock
    ) -> None:
        with _agents(), _summariser():
            await summary_service.run_rebuild(REBUILD_ID)

        storage.delete_summaries.assert_awaited_once()


class TestListSummaries:
    async def test_returns_every_stored_summary(
        self, summary_service: service.SummaryService, summaries: AsyncMock
    ) -> None:
        summaries.list_summaries.side_effect = lambda: [_make_summary()]

        response = await summary_service.list_summaries()

        assert len(response.items) == 1
        assert response.items[0].document_id == str(DOCUMENT_ID)
        assert response.items[0].title == "A Guide"

    async def test_gives_each_document_its_own_sections(
        self,
        summary_service: service.SummaryService,
        summaries: AsyncMock,
        sections: AsyncMock,
    ) -> None:
        other_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        summaries.list_summaries.side_effect = lambda: [
            _make_summary(),
            _make_summary(document_id=other_id, title="Another Guide"),
        ]
        sections.list_sections.side_effect = lambda: [
            _make_section(DOCUMENT_ID, "1", order=0),
            _make_section(other_id, "1", order=0),
            _make_section(DOCUMENT_ID, "2", order=1),
        ]

        response = await summary_service.list_summaries()

        first, second = response.items
        assert [entry.number for entry in first.sections] == ["1", "2"]
        assert [entry.number for entry in second.sections] == ["1"]
