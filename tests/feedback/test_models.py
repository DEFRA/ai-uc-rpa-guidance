"""Tests for feedback domain models: serialisation round-trips."""

import uuid
from datetime import UTC, datetime

from app.feedback import models


def _make_snapshot(**overrides: object) -> models.FindingSnapshot:
    defaults: dict[str, object] = {
        "agent": models.AgentName.CHECKER,
        "fields": {"issue": "Missing alt text", "category": "images_and_formatting"},
    }
    defaults.update(overrides)
    return models.FindingSnapshot(**defaults)  # type: ignore[arg-type]


def _make_entry(**overrides: object) -> models.FeedbackEntry:
    now = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
    defaults: dict[str, object] = {
        "id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "agent": models.AgentName.CHECKER,
        "finding_index": 0,
        "verdict": models.FeedbackVerdict.FIX,
        "comment": None,
        "finding_snapshot": _make_snapshot(),
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return models.FeedbackEntry(**defaults)  # type: ignore[arg-type]


class TestFeedbackEntryRoundTrip:
    def test_round_trips_finding_level_entry(self) -> None:
        entry = _make_entry()

        result = models.FeedbackEntry.from_mongo_doc(entry.to_document())

        assert result.id == entry.id
        assert result.job_id == entry.job_id
        assert result.agent == models.AgentName.CHECKER
        assert result.finding_index == 0
        assert result.verdict == models.FeedbackVerdict.FIX
        assert result.comment is None

    def test_round_trips_job_level_entry_with_none_finding_index(self) -> None:
        entry = _make_entry(finding_index=None)

        result = models.FeedbackEntry.from_mongo_doc(entry.to_document())

        assert result.finding_index is None

    def test_round_trips_comment(self) -> None:
        entry = _make_entry(comment="This is a genuine issue.")

        result = models.FeedbackEntry.from_mongo_doc(entry.to_document())

        assert result.comment == "This is a genuine issue."

    def test_round_trips_critic_agent(self) -> None:
        entry = _make_entry(
            agent=models.AgentName.CRITIC,
            finding_snapshot=_make_snapshot(
                agent=models.AgentName.CRITIC,
                fields={"what": "Wrong tone", "severity": "medium"},
            ),
        )

        result = models.FeedbackEntry.from_mongo_doc(entry.to_document())

        assert result.agent == models.AgentName.CRITIC

    def test_round_trips_all_verdicts(self) -> None:
        for verdict in models.FeedbackVerdict:
            entry = _make_entry(verdict=verdict)
            result = models.FeedbackEntry.from_mongo_doc(entry.to_document())
            assert result.verdict == verdict

    def test_round_trips_none_snapshot(self) -> None:
        entry = _make_entry(finding_snapshot=None)

        result = models.FeedbackEntry.from_mongo_doc(entry.to_document())

        assert result.finding_snapshot is None

    def test_round_trips_snapshot_fields(self) -> None:
        snapshot = _make_snapshot(
            fields={"issue": "Broken link", "section": "2.3", "category": "links"},
        )
        entry = _make_entry(finding_snapshot=snapshot)

        result = models.FeedbackEntry.from_mongo_doc(entry.to_document())

        assert result.finding_snapshot is not None
        assert result.finding_snapshot.fields["issue"] == "Broken link"
        assert result.finding_snapshot.fields["section"] == "2.3"

    def test_to_document_uses_underscore_id(self) -> None:
        entry = _make_entry()
        doc = entry.to_document()
        assert "_id" in doc
        assert "id" not in doc

    def test_to_document_serialises_enums_as_strings(self) -> None:
        entry = _make_entry(
            agent=models.AgentName.CHECKER,
            verdict=models.FeedbackVerdict.FALSE_POSITIVE,
        )
        doc = entry.to_document()
        assert doc["agent"] == "checker"
        assert doc["verdict"] == "false_positive"

    def test_to_document_serialises_snapshot_agent_as_string(self) -> None:
        entry = _make_entry(
            finding_snapshot=_make_snapshot(agent=models.AgentName.CRITIC)
        )
        doc = entry.to_document()
        assert doc["finding_snapshot"]["agent"] == "critic"
