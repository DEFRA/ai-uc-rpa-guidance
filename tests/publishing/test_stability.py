"""Tests for the run-to-run stability harness (scripts/publishing_stability.py).

The LLM judge is faked throughout — no live Bedrock call — so the structural
logic (blocking, clustering, aggregation, symmetry) is exercised deterministically.
"""

import asyncio
import json
from pathlib import Path

import pytest

from scripts import publishing_stability as stability


class RecordingJudge:
    """A fake judge returning a fixed score and remembering how it was called."""

    def __init__(self, score: float) -> None:
        self.score = score
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, first: str, second: str) -> float:
        self.calls.append((first, second))
        return self.score


def _finding(
    run: str,
    section: str,
    issue: str,
    category: str = "overall_publish_readiness",
    confidence: str = "high",
) -> stability.Finding:
    return stability.Finding(
        run=run,
        category=category,
        section=section,
        issue=issue,
        severity="medium",
        confidence=confidence,
    )


def _cluster(section_key: str, members: list[stability.Finding]) -> stability.Cluster:
    return stability.Cluster(section_key=section_key, members=members)


# --- pure helpers -----------------------------------------------------------


def test_connected_components_groups_transitively() -> None:
    """Edges link a-b and b-c, so a, b and c form one component; d stands alone."""
    components = stability.connected_components(4, [(0, 1), (1, 2)])
    assert {frozenset(c) for c in components} == {frozenset({0, 1, 2}), frozenset({3})}


def test_section_key_is_the_number_despite_drifting_title() -> None:
    """The stable section number keys the block even when the trailing title drifts."""
    branch = _finding("run01", "Section 4.1.1 Check Old link — 'No' branch", "x")
    route = _finding("run02", "Section 4.1.1 Check Old link — 'No' route", "x")
    assert branch.section_key == route.section_key == "4.1.1"


def test_section_key_falls_back_to_text_when_unnumbered() -> None:
    """A digit-less section keys on its normalised text, not a number."""
    assert _finding("run01", "Annex", "x").section_key == "text:annex"


# --- tiered same-problem score ---------------------------------------------


async def test_high_jaccard_matches_without_judging() -> None:
    """Identical text clears the high gate, so the judge is never consulted."""
    judge = RecordingJudge(0.0)
    score = await stability.same_problem_score(
        "broken link in tenure", "broken link in tenure", judge, low=0.1, high=0.6
    )
    assert score == 1.0
    assert judge.calls == []


async def test_low_jaccard_rejects_without_judging() -> None:
    """Disjoint text falls at or below the low gate, so the judge is never consulted."""
    judge = RecordingJudge(1.0)
    score = await stability.same_problem_score(
        "heading not styled", "telephone number wrong", judge, low=0.1, high=0.6
    )
    assert score == 0.0
    assert judge.calls == []


async def test_middle_band_consults_judge_with_canonical_order() -> None:
    """An ambiguous pair is judged, and the judge always sees the sorted pair."""
    judge = RecordingJudge(0.7)
    forward = await stability.same_problem_score(
        "alpha beta gamma", "alpha beta delta", judge, low=0.1, high=0.6
    )
    reversed_ = await stability.same_problem_score(
        "alpha beta delta", "alpha beta gamma", judge, low=0.1, high=0.6
    )
    assert forward == reversed_ == 0.7
    # Both orderings reach the judge as the same canonical (sorted) pair.
    assert judge.calls == [
        ("alpha beta delta", "alpha beta gamma"),
        ("alpha beta delta", "alpha beta gamma"),
    ]


# --- clustering -------------------------------------------------------------


async def test_identical_runs_agree_completely() -> None:
    """Two runs with the same findings cluster pairwise; agreement is 1.0."""
    findings = [
        _finding("run01", "Section 4.1 Tenure", "tenure link is broken"),
        _finding("run01", "Section 7.1 Telephone", "phone number is wrong"),
        _finding("run02", "Section 4.1 Tenure", "tenure link is broken"),
        _finding("run02", "Section 7.1 Telephone", "phone number is wrong"),
    ]
    clusters = await stability.build_clusters(findings, RecordingJudge(0.0))
    assert len(clusters) == 2
    assert all(cluster.support == 2 for cluster in clusters)
    agreements = stability.pairwise_agreements(["run01", "run02"], clusters)
    assert agreements[("run01", "run02")] == 1.0


async def test_on_section_fires_once_per_block_with_totals() -> None:
    """The progress callback reports each section block and a known total up front."""
    findings = [
        _finding("run01", "Section 4.1 Tenure", "a"),
        _finding("run02", "Section 4.1 Tenure", "a"),
        _finding("run01", "Section 7.1 Telephone", "b"),
    ]
    calls: list[tuple[int, int, str, int]] = []

    def record(number: int, total: int, section_key: str, in_block: int) -> None:
        calls.append((number, total, section_key, in_block))

    await stability.build_clusters(findings, RecordingJudge(0.0), on_section=record)
    # Two section blocks (4.1 with two findings, 7.1 with one); total is 2 throughout.
    assert calls == [(1, 2, "4.1", 2), (2, 2, "7.1", 1)]


class ConcurrencyProbe:
    """A judge that records the peak number of simultaneous in-flight calls."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def __call__(self, _first: str, _second: str) -> float:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0)  # yield so other admitted calls can overlap
        self.active -= 1
        return 1.0


async def test_concurrency_bounds_simultaneous_judge_calls() -> None:
    """The semaphore caps overlapping judge calls at the requested concurrency."""
    # One section block of 4 findings → 6 pairs; low/high forced so every pair judges.
    findings = [_finding("run01", "Section 4.1", f"issue {i}") for i in range(4)]
    probe = ConcurrencyProbe()
    await stability.build_clusters(
        findings, probe, low=-1.0, high=2.0, threshold=0.5, concurrency=2
    )
    assert probe.peak == 2


async def test_disjoint_sections_do_not_agree() -> None:
    """Findings in different sections never share a block, so agreement is 0.0."""
    findings = [
        _finding("run01", "Section 1 Intro", "problem here"),
        _finding("run02", "Section 2 Body", "different problem"),
    ]
    clusters = await stability.build_clusters(findings, RecordingJudge(1.0))
    assert len(clusters) == 2
    assert all(cluster.support == 1 for cluster in clusters)
    agreements = stability.pairwise_agreements(["run01", "run02"], clusters)
    assert agreements[("run01", "run02")] == 0.0


async def test_judge_merges_a_middle_band_phrasing_variant() -> None:
    """Same section, paraphrased issue: the judge's verdict decides the merge."""
    findings = [
        _finding("run01", "Section 4.1 Tenure", "alpha beta gamma"),
        _finding("run02", "Section 4.1 Tenure", "alpha beta delta"),
    ]
    merging = await stability.build_clusters(findings, RecordingJudge(1.0))
    assert len(merging) == 1
    assert merging[0].support == 2

    splitting = await stability.build_clusters(findings, RecordingJudge(0.0))
    assert len(splitting) == 2


# --- aggregation ------------------------------------------------------------


def test_pairwise_agreement_is_soft_dice_over_supports() -> None:
    """Run A shares one of its two issues with run B's one issue: 2*1/(2+1)."""
    clusters = [
        _cluster(
            "4.1",
            [_finding("A", "Section 4.1", "x"), _finding("B", "Section 4.1", "x")],
        ),
        _cluster("7.1", [_finding("A", "Section 7.1", "y")]),
    ]
    agreements = stability.pairwise_agreements(["A", "B"], clusters)
    assert agreements[("A", "B")] == pytest.approx(2 / 3)


def test_section_sort_key_orders_numbers_naturally() -> None:
    """Text-keyed sections lead; dotted numbers order numerically (10 after 9)."""
    keys = ["10", "2", "4.1.1", "4.1", "text:annex"]
    assert sorted(keys, key=stability._section_sort_key) == [
        "text:annex",
        "2",
        "4.1",
        "4.1.1",
        "10",
    ]


def test_support_histogram_counts_each_level() -> None:
    """Clusters are tallied by how many distinct runs they appear in."""
    clusters = [
        _cluster("4.1", [_finding("A", "s", "x"), _finding("B", "s", "x")]),
        _cluster("7.1", [_finding("A", "s", "y")]),
    ]
    assert stability.support_histogram(clusters, n_runs=2) == {1: 1, 2: 1}


# --- category filter --------------------------------------------------------


def test_throttle_is_detected_from_exception_text() -> None:
    """Rate-limit exceptions are recognised; unrelated ones are not."""

    class ThrottlingException(Exception):  # noqa: N818 — the real boto exception name
        pass

    # Matched on the type name (message is unremarkable)...
    assert stability._is_throttle(ThrottlingException("request failed"))
    # ...or on the message.
    assert stability._is_throttle(RuntimeError("HTTP 429 Too Many Requests"))
    assert not stability._is_throttle(ValueError("malformed response"))


def test_unknown_excluded_category_is_rejected() -> None:
    with pytest.raises(SystemExit):
        stability.parse_excluded_categories("links,not_a_category")


def test_valid_excluded_categories_parse() -> None:
    assert stability.parse_excluded_categories("links, images_and_formatting") == {
        "links",
        "images_and_formatting",
    }


def test_load_findings_drops_excluded_categories(tmp_path: Path) -> None:
    """The category filter is applied at load, and the run label is derived."""
    path = tmp_path / "doc-run07.json"
    path.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "category": "links",
                        "section": "Section 2",
                        "issue": "a",
                        "confidence": "high",
                    },
                    {
                        "category": "headings_and_layout",
                        "section": "Section 2",
                        "issue": "b",
                        "confidence": "moderate",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    findings = stability.load_findings(path, excluded={"links"})
    assert [(f.run, f.category, f.issue) for f in findings] == [
        ("run07", "headings_and_layout", "b")
    ]
