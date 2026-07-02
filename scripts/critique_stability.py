#!/usr/bin/env python3
"""Measure the critique checker's run-to-run reproducibility (no ground truth).

The sibling of ``publishing_stability.py``, honouring the critique checker's shape:
each run's result carries one report per standard (``gds`` / ``defra_style``) and a
finding locates itself with a free-text ``where`` that may name several sections —
or none ("throughout").

Standards are a hard partition: pairing, judging, clustering and reporting all
happen within a single standard's findings. A gds finding is never compared with a
defra_style one.

A finding is one node however many sections it names. Two same-standard findings
are candidate matches when their extracted section-number sets intersect; a finding
whose ``where`` names no section number is a wildcard that may pair with any
finding in its standard. Candidates are scored as same-problem on their ``what``
texts (lexical Jaccard gate; a symmetric LLM judge only for the ambiguous middle
band) and connected components become the distinct issues — so a broad finding
spanning many sections still counts once.

Usage:
  # Compare captured run files:
  uv run scripts/critique_stability.py \
      run01.json run02.json run03.json [--standards gds,defra_style] \
      [--low F] [--high F] [--threshold F] [--concurrency N]

  # Or generate the runs first (needs the critique service running):
  uv run scripts/critique_stability.py \
      --document input.docx [--runs N] [--run-concurrency N] [--concurrency N]

Each input is a captured critique result (a full job result or a bare critique
response) with a ``reports`` list. Run generation and the stability comparison have
separate concurrency controls: ``--run-concurrency`` bounds critiques in flight,
``--concurrency`` bounds judge calls.
"""

import argparse
import asyncio
import json
import re
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from scripts.console import dim, set_colour
from scripts.publishing_common import extract_section_numbers, issue_jaccard
from scripts.publishing_runs import (
    CRITIQUE_JOBS_PATH,
    CRITIQUE_TIMEOUT_S,
    DEFAULT_HOST,
    DEFAULT_UPLOADER,
    generate_runs,
)
from scripts.publishing_runs import (
    DEFAULT_CONCURRENCY as DEFAULT_RUN_CONCURRENCY,
)
from scripts.stability_common import (
    DEFAULT_CONCURRENCY,
    DEFAULT_HIGH_JACCARD,
    DEFAULT_LOW_JACCARD,
    DEFAULT_RUNS,
    DEFAULT_THRESHOLD,
    JudgeFn,
    JudgeUsage,
    bounded_judge,
    connected_components,
    dotted_tuple,
    make_bedrock_judge,
    match_report_path,
    pairwise_agreements,
    progress,
    run_label,
    score_edges,
    support_colour,
    support_histogram,
    truncate,
    write_match_sheet,
)

STANDARDS = ("gds", "defra_style")

# Progress callback: (pool number, total pools, standard, findings, candidate pairs).
# Called as each standard's pool starts — the natural, known-ahead unit of work.
StandardProgress = Callable[[int, int, str, int, int], None]


@dataclass(frozen=True)
class Finding:
    """One finding from one run's report, reduced to the fields used for comparison.

    ``sections`` holds every section number extracted from ``where``; empty means
    the finding gave no section number (e.g. "throughout") and pairs as a wildcard.
    """

    run: str
    standard: str
    rule_reference: str
    what: str
    where: str
    severity: str
    sections: tuple[str, ...]


@dataclass
class Cluster:
    """A group of findings judged to be the same issue, drawn from one or more runs."""

    standard: str
    members: list[Finding]

    @property
    def runs(self) -> set[str]:
        return {member.run for member in self.members}

    @property
    def support(self) -> int:
        """Number of distinct runs this issue appears in."""
        return len(self.runs)

    @property
    def severities(self) -> list[str]:
        """Sorted unique severities across all members."""
        return sorted({member.severity for member in self.members})

    @property
    def rule_references(self) -> list[str]:
        """Sorted unique rule references across all members."""
        return sorted({member.rule_reference for member in self.members})

    @property
    def sections(self) -> list[str]:
        """The union of the members' section numbers, in document order."""
        return sorted(
            {number for member in self.members for number in member.sections},
            key=dotted_tuple,
        )

    def representative(self) -> Finding:
        """The medoid member — the ``what`` text most typical of the cluster."""
        if len(self.members) == 1:
            return self.members[0]
        return max(
            self.members,
            key=lambda m: sum(
                issue_jaccard(m.what, other.what)
                for other in self.members
                if other is not m
            ),
        )


@dataclass
class StabilityReport:
    """The computed stability of a set of runs, partitioned by standard."""

    runs: list[str]
    standards: tuple[str, ...]
    findings_per_run: dict[str, dict[str, int]]
    clusters: dict[str, list[Cluster]]
    judge_usage: JudgeUsage | None = None

    @property
    def all_clusters(self) -> list[Cluster]:
        """Every standard's clusters together (they never span standards)."""
        return [
            cluster
            for standard in self.standards
            for cluster in self.clusters.get(standard, [])
        ]


def parse_standards(raw: str | None) -> tuple[str, ...]:
    """Validate a comma-separated standards list, defaulting to all of them."""
    if not raw:
        return STANDARDS
    requested = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = sorted(requested - set(STANDARDS))
    if unknown:
        message = f"unknown standards {unknown}; valid standards are {list(STANDARDS)}"
        raise SystemExit(message)
    return tuple(standard for standard in STANDARDS if standard in requested)


def load_findings(path: Path, standards: tuple[str, ...]) -> list[Finding]:
    """Load one run's findings from its per-standard reports, keeping ``standards``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    run = run_label(path)
    findings: list[Finding] = []
    for report in data.get("reports", []):
        standard = str(report.get("standard", ""))
        if standard not in standards:
            continue
        for finding in report.get("findings", []):
            where = str(finding.get("where", ""))
            findings.append(
                Finding(
                    run=run,
                    standard=standard,
                    rule_reference=str(finding.get("rule_reference", "")),
                    what=str(finding.get("what", "")),
                    where=where,
                    severity=str(finding.get("severity", "")),
                    sections=tuple(extract_section_numbers(where)),
                )
            )
    return findings


def candidate_pairs(pool: list[Finding]) -> list[tuple[int, int]]:
    """Index pairs within one standard's pool that are worth comparing.

    Two findings are candidates when their section sets intersect, or either names
    no section at all (a wildcard: a "throughout" finding overlaps everything in
    its standard). This replaces publishing's hard blocking by section — a critique
    finding may span several sections, so overlap, not equality, is the gate.
    """
    return [
        (i, j)
        for i, j in combinations(range(len(pool)), 2)
        if not pool[i].sections
        or not pool[j].sections
        or set(pool[i].sections) & set(pool[j].sections)
    ]


async def build_clusters(
    pools: dict[str, list[Finding]],
    judge: JudgeFn,
    low: float = DEFAULT_LOW_JACCARD,
    high: float = DEFAULT_HIGH_JACCARD,
    threshold: float = DEFAULT_THRESHOLD,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_standard: StandardProgress | None = None,
) -> dict[str, list[Cluster]]:
    """Cluster each standard's findings into distinct issues, independently.

    The per-standard pools are the zero-crosstalk partition: candidate pairing,
    judging and components never leave a pool. ``concurrency`` bounds how many
    judge calls run at once; ``on_standard`` (if given) is called as each pool
    begins, with its size and candidate pair count known up front.
    """
    bounded = bounded_judge(judge, concurrency)
    clusters: dict[str, list[Cluster]] = {}
    for number, (standard, pool) in enumerate(pools.items(), start=1):
        pairs = candidate_pairs(pool)
        if on_standard is not None:
            on_standard(number, len(pools), standard, len(pool), len(pairs))
        edges = await score_edges(
            [finding.what for finding in pool], pairs, bounded, low, high, threshold
        )
        clusters[standard] = [
            Cluster(standard, [pool[index] for index in component])
            for component in connected_components(len(pool), edges)
        ]
    return clusters


def _cluster_sort_key(cluster: Cluster) -> tuple[int, str, tuple[int, ...]]:
    """Order clusters naturally: number-less first, then by first section number.

    Mirrors publishing's section ordering — dotted decimals sort numerically, and a
    uniformly typed tuple keeps numbered and number-less clusters comparable (the
    latter ordered by their representative's ``where`` text).
    """
    sections = cluster.sections
    if not sections:
        return (0, cluster.representative().where.casefold().strip(), ())
    return (1, "", dotted_tuple(sections[0]))


def sections_display(cluster: Cluster) -> str:
    """A cluster's location for display: its section numbers, else the where text."""
    if cluster.sections:
        return ",".join(cluster.sections)
    return truncate(cluster.representative().where, 40)


def _print_standard(standard: str, clusters: list[Cluster], runs: list[str]) -> None:
    """Print one standard's agreement, support histogram and per-issue table."""
    n_runs = len(runs)
    print(f"\n=== {standard} ===")

    agreements = list(pairwise_agreements(runs, list(clusters)).values())
    mean = sum(agreements) / len(agreements) if agreements else 1.0
    worst = min(agreements) if agreements else 1.0
    # Population SD: the run pairs are the whole set being summarised, not a sample.
    spread = statistics.pstdev(agreements) if len(agreements) > 1 else 0.0
    print(
        f"Pairwise agreement (soft Dice over {len(agreements)} run pairs): "
        f"mean {mean:.3f}   sd {spread:.3f}   min {worst:.3f}"
    )

    histogram = support_histogram(list(clusters), n_runs)
    total = len(clusters)
    stable = histogram.get(n_runs, 0)
    print(f"Distinct issues: {total}   in all {n_runs} runs: {stable}")
    for support in range(n_runs, 0, -1):
        print(dim(f"  in {support}/{n_runs} runs: {histogram.get(support, 0)}"))

    print("Per-issue consistency (most stable first):")
    ordered = sorted(clusters, key=lambda c: (-c.support, _cluster_sort_key(c)))
    for cluster in ordered:
        representative = cluster.representative()
        ratio = support_colour(cluster.support, n_runs, f"{cluster.support}/{n_runs}")
        rules = truncate(", ".join(cluster.rule_references), 40)
        severities = ", ".join(cluster.severities)
        print(
            f"  {ratio}  §{sections_display(cluster)}  [{rules}]  [{severities}]  "
            f"{truncate(representative.what)}"
        )


def print_report(report: StabilityReport) -> None:
    """Print the per-standard stability sections and the combined summary."""
    n_runs = len(report.runs)
    print(f"Runs: {n_runs}")
    for run in report.runs:
        counts = report.findings_per_run[run]
        parts = ", ".join(
            f"{standard} {counts.get(standard, 0)}" for standard in report.standards
        )
        print(dim(f"  {run}: {parts} findings"))

    for standard in report.standards:
        _print_standard(standard, report.clusters.get(standard, []), report.runs)

    if len(report.standards) > 1:
        combined = report.all_clusters
        agreements = list(pairwise_agreements(report.runs, list(combined)).values())
        mean = sum(agreements) / len(agreements) if agreements else 1.0
        print(
            f"\nOverall: distinct issues {len(combined)}   "
            f"pairwise agreement mean {mean:.3f}"
        )

    if report.judge_usage is not None:
        usage = report.judge_usage
        print(
            dim(
                f"Judge tokens over {usage.calls} calls: "
                f"{usage.input_tokens} in, {usage.output_tokens} out "
                f"({usage.input_tokens + usage.output_tokens} total)"
            )
        )


# Trailing "-critique-<batch timestamp>-runNN" tail that generate_runs appends to
# the input stem; stripped to recover the original document name for the report
# filename. Critique captures have always carried the checker infix.
_RUN_FILE_SUFFIX = re.compile(r"-critique-\d{8}T\d{6}Z-run\d+$")


def match_report_rows(
    clusters: list[Cluster], runs: list[str]
) -> list[tuple[str, float, list[str]]]:
    """One row per cluster: sections, match fraction, then each run's ``what`` text.

    Clusters are laid out in section order so each row reads as one issue with every
    run's wording of it side by side; a run that did not raise the issue leaves a
    blank cell. A run contributing two findings to one cluster has them joined
    with ' | '.
    """
    n_runs = len(runs)
    ordered = sorted(clusters, key=lambda c: (_cluster_sort_key(c), -c.support))
    rows: list[tuple[str, float, list[str]]] = []
    for cluster in ordered:
        by_run: dict[str, list[str]] = {}
        for member in cluster.members:
            by_run.setdefault(member.run, []).append(member.what)
        cells = [" | ".join(dict.fromkeys(by_run.get(run, []))) for run in runs]
        rows.append(
            (
                sections_display(cluster),
                cluster.support / n_runs if n_runs else 0.0,
                cells,
            )
        )
    return rows


def write_match_report(report: StabilityReport, path: Path) -> None:
    """Write the per-run match report to ``path``, one worksheet per standard.

    The per-sheet split is the standards partition: a sheet only ever holds its
    own standard's issues.
    """
    import openpyxl

    wb = openpyxl.Workbook()
    for index, standard in enumerate(report.standards):
        sheet = wb.active if index == 0 else wb.create_sheet()
        sheet.title = standard
        write_match_sheet(
            sheet,
            report.runs,
            match_report_rows(report.clusters.get(standard, []), report.runs),
        )
    wb.save(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "run_files",
        nargs="*",
        metavar="RUN_FILE",
        help=(
            "Two or more captured run JSON files for one document. Omit and use "
            "--document/--runs to generate the runs instead."
        ),
    )
    parser.add_argument(
        "--document",
        default=None,
        help="Generate runs by critiquing this .docx --runs times instead of passing "
        "RUN_FILEs (needs the critique service running).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help=(
            "Number of runs to generate from --document, two or more "
            f"(default: {DEFAULT_RUNS})."
        ),
    )
    parser.add_argument(
        "--run-concurrency",
        type=int,
        default=DEFAULT_RUN_CONCURRENCY,
        help=(
            "Max critiques in flight when generating runs — distinct from "
            f"--concurrency (default: {DEFAULT_RUN_CONCURRENCY})."
        ),
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Backend base URL (default: {DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--uploader",
        default=DEFAULT_UPLOADER,
        help=f"CDP uploader base URL (default: {DEFAULT_UPLOADER}).",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for generated run files (default: the document's directory).",
    )
    parser.add_argument(
        "--standards",
        default=None,
        help=(
            "Comma-separated standards to compare, e.g. 'gds'. "
            f"Valid: {', '.join(STANDARDS)} (default: all)."
        ),
    )
    parser.add_argument(
        "--low",
        type=float,
        default=DEFAULT_LOW_JACCARD,
        help=f"Jaccard at or below which issues are unrelated (default: {DEFAULT_LOW_JACCARD}).",
    )
    parser.add_argument(
        "--high",
        type=float,
        default=DEFAULT_HIGH_JACCARD,
        help=f"Jaccard at or above which issues match without judging (default: {DEFAULT_HIGH_JACCARD}).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Same-problem score needed to cluster two issues (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=(
            "Max judge (LLM) calls in flight at once within a standard's pool "
            f"(default: {DEFAULT_CONCURRENCY}; 1 = sequential)."
        ),
    )
    parser.add_argument(
        "--match-report",
        action="store_true",
        help=(
            "Also write a per-run match report Excel workbook, one sheet per "
            "standard (<input>-match-report-<ts>.xlsx), to --out-dir or the "
            "input's directory."
        ),
    )
    parser.add_argument(
        "--no-colour",
        action="store_true",
        help="Disable ANSI colour (default: on when stdout is a TTY).",
    )
    return parser.parse_args()


async def _generate_run_files(args: argparse.Namespace) -> list[Path]:
    """Generate the run files from --document, with progress to stderr."""
    if args.run_files:
        message = "pass RUN_FILEs or --document, not both"
        raise SystemExit(message)
    runs = args.runs if args.runs is not None else DEFAULT_RUNS
    if runs < 2:
        message = "--runs must be at least 2 when generating from --document"
        raise SystemExit(message)
    if args.run_concurrency < 1:
        message = "--run-concurrency must be at least 1"
        raise SystemExit(message)
    document = Path(args.document)
    out_dir = Path(args.out_dir) if args.out_dir else document.resolve().parent
    progress(
        f"generating {runs} runs from {document.name} "
        f"(run-concurrency {args.run_concurrency})"
    )
    return await generate_runs(
        document,
        runs=runs,
        concurrency=args.run_concurrency,
        host=args.host,
        uploader=args.uploader,
        out_dir=out_dir,
        checker="critique",
        submit_path=CRITIQUE_JOBS_PATH,
        jobs_path=CRITIQUE_JOBS_PATH,
        timeout_s=CRITIQUE_TIMEOUT_S,
        on_run=lambda done, total: progress(f"generated run {done}/{total}"),
    )


def _existing_run_files(args: argparse.Namespace) -> list[Path]:
    """Validate the RUN_FILE positionals for comparison."""
    if args.runs is not None:
        message = "--runs only applies when generating with --document"
        raise SystemExit(message)
    paths = [Path(path) for path in args.run_files]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        message = f"run files not found: {missing}"
        raise SystemExit(message)
    if len(paths) < 2:
        message = "need at least two run files to compare (or generate with --document)"
        raise SystemExit(message)
    return paths


async def main() -> None:
    args = parse_args()
    set_colour(sys.stdout.isatty() and not args.no_colour)

    if args.concurrency < 1:
        message = "--concurrency must be at least 1"
        raise SystemExit(message)
    standards = parse_standards(args.standards)
    if args.document is not None:
        paths = await _generate_run_files(args)
    else:
        paths = _existing_run_files(args)

    per_run = {run_label(path): load_findings(path, standards) for path in paths}
    # The zero-crosstalk partition: one pool per standard, built once, up front.
    pools: dict[str, list[Finding]] = {standard: [] for standard in standards}
    for findings in per_run.values():
        for finding in findings:
            pools[finding.standard].append(finding)

    # Load the project .env (searching up from the cwd) before importing the model,
    # which reads Bedrock config from env at import time.
    load_dotenv(find_dotenv(usecwd=True))
    from app.infra.bedrock import llm

    judge, judge_usage = make_bedrock_judge(llm.claude_sonnet)

    def report_standard(
        number: int, total: int, standard: str, in_pool: int, pairs: int
    ) -> None:
        progress(
            f"standard {number}/{total}: {standard} ({in_pool} findings, "
            f"{pairs} candidate pairs) — {judge_usage.calls} judge calls so far"
        )

    clusters = await build_clusters(
        pools,
        judge,
        args.low,
        args.high,
        args.threshold,
        concurrency=args.concurrency,
        on_standard=report_standard,
    )
    report = StabilityReport(
        runs=list(per_run),
        standards=standards,
        findings_per_run={
            run: {
                standard: sum(1 for f in findings if f.standard == standard)
                for standard in standards
            }
            for run, findings in per_run.items()
        },
        clusters=clusters,
        judge_usage=judge_usage,
    )
    print_report(report)
    if args.match_report:
        path = match_report_path(args.document, args.out_dir, paths, _RUN_FILE_SUFFIX)
        write_match_report(report, path)
        progress(f"wrote match report {path}")


if __name__ == "__main__":
    asyncio.run(main())
