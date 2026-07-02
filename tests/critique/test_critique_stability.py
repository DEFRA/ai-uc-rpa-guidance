"""Tests for the critique stability harness (scripts/critique_stability.py).

The LLM judge is faked throughout — no live Bedrock call — so the critique-specific
structure is exercised deterministically: the per-standard partition (zero
crosstalk), section-set overlap pairing with number-less wildcards, and the
one-node-per-finding clustering that keeps a broad finding a single issue.
"""

import json
from pathlib import Path

import pytest

from scripts import critique_stability as stability
from scripts import stability_common as common


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
    where: str,
    what: str,
    standard: str = "gds",
    severity: str = "medium",
    rule_reference: str = "bold",
) -> stability.Finding:
    from scripts.publishing_common import extract_section_numbers

    return stability.Finding(
        run=run,
        standard=standard,
        rule_reference=rule_reference,
        what=what,
        where=where,
        severity=severity,
        sections=tuple(extract_section_numbers(where)),
    )


def _cluster(members: list[stability.Finding]) -> stability.Cluster:
    return stability.Cluster(standard=members[0].standard, members=members)


# --- standards ---------------------------------------------------------------


def test_parse_standards_defaults_to_all() -> None:
    assert stability.parse_standards(None) == ("gds", "defra_style")


def test_parse_standards_normalises_order() -> None:
    """A subset parses, and always comes back in the canonical standard order."""
    assert stability.parse_standards("defra_style,gds") == ("gds", "defra_style")
    assert stability.parse_standards("defra_style") == ("defra_style",)


def test_parse_standards_rejects_unknown() -> None:
    with pytest.raises(SystemExit):
        stability.parse_standards("gds,not_a_standard")


# --- loading -----------------------------------------------------------------


def test_load_findings_reads_reports_and_extracts_sections(tmp_path: Path) -> None:
    """Findings gain their report's standard and the section numbers in `where`."""
    path = tmp_path / "doc-critique-run03.json"
    path.write_text(
        json.dumps(
            {
                "reports": [
                    {
                        "standard": "gds",
                        "findings": [
                            {"where": "Sections 2, 3 and 4.1", "what": "a"},
                            {"where": "Throughout — section headings", "what": "b"},
                        ],
                    },
                    {
                        "standard": "defra_style",
                        "findings": [{"where": "Section 1 Introduction", "what": "c"}],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    findings = stability.load_findings(path, ("gds", "defra_style"))
    assert [(f.run, f.standard, f.what, f.sections) for f in findings] == [
        ("run03", "gds", "a", ("2", "3", "4.1")),
        ("run03", "gds", "b", ()),
        ("run03", "defra_style", "c", ("1",)),
    ]


def test_load_findings_filters_to_requested_standards(tmp_path: Path) -> None:
    path = tmp_path / "doc-critique-run01.json"
    path.write_text(
        json.dumps(
            {
                "reports": [
                    {"standard": "gds", "findings": [{"where": "Section 2"}]},
                    {"standard": "defra_style", "findings": [{"where": "Section 1"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    findings = stability.load_findings(path, ("defra_style",))
    assert [f.standard for f in findings] == ["defra_style"]


# --- candidate pairing -------------------------------------------------------


def test_candidate_pairs_require_section_overlap() -> None:
    """Intersecting section sets pair; disjoint ones never meet."""
    pool = [
        _finding("run01", "Sections 2, 3 and 4", "a"),
        _finding("run02", "Sections 4 and 5", "b"),
        _finding("run03", "Section 7", "c"),
    ]
    # Only the {2,3,4} × {4,5} pair shares a section; §7 pairs with neither.
    assert stability.candidate_pairs(pool) == [(0, 1)]


def test_numberless_where_is_a_wildcard() -> None:
    """A finding with no section number may pair with anything in its standard."""
    pool = [
        _finding("run01", "Section 2", "a"),
        _finding("run02", "Throughout", "b"),
        _finding("run03", "Section 7", "c"),
    ]
    assert stability.candidate_pairs(pool) == [(0, 1), (1, 2)]


# --- clustering --------------------------------------------------------------


async def test_broad_finding_clusters_once() -> None:
    """A multi-section finding is one node: one cluster, not one per section."""
    pools = {
        "gds": [
            _finding("run01", "Sections 2, 3, 4 and 5", "bold used for emphasis"),
            _finding("run02", "Sections 2 and 3", "bold used for emphasis"),
        ]
    }
    clusters = await stability.build_clusters(pools, RecordingJudge(0.0))
    assert len(clusters["gds"]) == 1
    assert clusters["gds"][0].support == 2
    assert clusters["gds"][0].sections == ["2", "3", "4", "5"]


async def test_standards_never_cross() -> None:
    """Identical findings under different standards stay in separate clusters."""
    pools = {
        "gds": [_finding("run01", "Section 2", "bold used for emphasis", "gds")],
        "defra_style": [
            _finding("run02", "Section 2", "bold used for emphasis", "defra_style")
        ],
    }
    judge = RecordingJudge(1.0)
    clusters = await stability.build_clusters(pools, judge)
    assert len(clusters["gds"]) == 1
    assert len(clusters["defra_style"]) == 1
    # Nothing ever needed judging: the pools are compared independently.
    assert judge.calls == []


async def test_on_standard_reports_pools_and_pairs_up_front() -> None:
    """The progress callback sees each standard's pool and candidate pair count."""
    pools = {
        "gds": [
            _finding("run01", "Section 2", "a"),
            _finding("run02", "Section 2", "a"),
        ],
        "defra_style": [],
    }
    calls: list[tuple[int, int, str, int, int]] = []

    def record(number: int, total: int, standard: str, size: int, pairs: int) -> None:
        calls.append((number, total, standard, size, pairs))

    await stability.build_clusters(pools, RecordingJudge(0.0), on_standard=record)
    assert calls == [(1, 2, "gds", 2, 1), (2, 2, "defra_style", 0, 0)]


# --- cluster presentation ----------------------------------------------------


def test_cluster_sections_union_in_document_order() -> None:
    """Sections are the members' union, ordered numerically (10 after 9)."""
    cluster = _cluster(
        [
            _finding("run01", "Sections 9 and 10", "x"),
            _finding("run02", "Sections 4.1 and 9", "x"),
        ]
    )
    assert cluster.sections == ["4.1", "9", "10"]


def test_sections_display_falls_back_to_where_text() -> None:
    """Numbered clusters show their numbers; number-less ones their where text."""
    numbered = _cluster([_finding("run01", "Sections 2 and 3", "x")])
    assert stability.sections_display(numbered) == "2,3"
    numberless = _cluster([_finding("run01", "Throughout — headings", "x")])
    assert stability.sections_display(numberless) == "Throughout — headings"


def test_cluster_sort_key_puts_numberless_first_then_numeric() -> None:
    clusters = [
        _cluster([_finding("run01", "Section 10", "x")]),
        _cluster([_finding("run01", "Section 2", "x")]),
        _cluster([_finding("run01", "Throughout", "x")]),
    ]
    ordered = sorted(clusters, key=stability._cluster_sort_key)
    assert [stability.sections_display(c) for c in ordered] == [
        "Throughout",
        "2",
        "10",
    ]


# --- match report ------------------------------------------------------------


def test_match_report_rows_lays_runs_side_by_side_per_standard() -> None:
    """Each row is one issue: sections, fraction, then every run's wording or a blank."""
    runs = ["run01", "run02"]
    clusters = [
        _cluster(
            [
                _finding("run01", "Sections 2 and 3", "bold overused"),
                _finding("run02", "Section 3", "bold used decoratively"),
            ]
        ),
        _cluster([_finding("run01", "Section 5", "sentence too long")]),
    ]
    rows = stability.match_report_rows(clusters, runs)
    assert rows[0] == ("2,3", 1.0, ["bold overused", "bold used decoratively"])
    assert rows[1] == ("5", pytest.approx(1 / 2), ["sentence too long", ""])


def test_match_report_path_strips_critique_suffix() -> None:
    paths = [Path("/runs/input-critique-20260702T162435Z-run01.json")]
    path = common.match_report_path(None, None, paths, stability._RUN_FILE_SUFFIX)
    assert path.parent == Path("/runs")
    assert path.name.startswith("input-match-report-")
    assert path.suffix == ".xlsx"
