#!/usr/bin/env python3
"""Measure the publishing checker's run-to-run reproducibility (no ground truth).

The checker is non-deterministic: the same document analysed twice yields
overlapping-but-different findings. This harness quantifies that overlap across N
captured run outputs, with no human ground truth — only the runs themselves.

A finding reduces, for comparison, to ``(section number, issue text)``. The section
number is assumed stable (the trailing section title may drift run to run), so it is
a free, reliable blocking key; the only judgement needed is whether two free-text
issue descriptions name the same underlying problem.

Pipeline:
  0. Filter — drop findings whose category is in ``--exclude-categories`` (the only
     use of category; it is a coarse source filter, never a comparison key).
  1. Block — group every run's findings by section number.
  2. Cluster — within a block, draw an edge between two findings when they describe
     the same problem (lexical Jaccard gate; a symmetric LLM judge only for the
     ambiguous middle band), then take connected components. Each cluster is one
     distinct issue; its support = the number of distinct runs it appears in.
  3. Report — per-finding consistency (support k/N: which findings are stable vs
     flaky) plus an aggregate agreement score derived from the cluster supports.

Usage:
  # Compare captured run files:
  uv run scripts/publishing_stability.py \
      run01.json run02.json run03.json [--exclude-categories links] \
      [--low F] [--high F] [--threshold F] [--concurrency N]

  # Or generate the runs first (needs the publishing service running):
  uv run scripts/publishing_stability.py \
      --document input.docx [--runs N] [--run-concurrency N] [--concurrency N]

Each input is an analyse response (or captured run file) with a ``findings`` list.
Run generation and the stability comparison have separate concurrency controls:
``--run-concurrency`` bounds analyses in flight, ``--concurrency`` bounds judge calls.
"""

import argparse
import asyncio
import json
import re
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from app.publishing.models import FindingCategory
from scripts.console import dim, set_colour
from scripts.publishing_common import extract_section_number, issue_jaccard
from scripts.publishing_runs import (
    DEFAULT_CONCURRENCY as DEFAULT_RUN_CONCURRENCY,
)
from scripts.publishing_runs import (
    DEFAULT_HOST,
    DEFAULT_UPLOADER,
    generate_runs,
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

# Progress callback: (block number, total blocks, section key, findings in block).
# Called as each section block starts — the natural, known-ahead unit of work.
SectionProgress = Callable[[int, int, str, int], None]


@dataclass(frozen=True)
class Finding:
    """One finding from one run, reduced to the fields used for comparison."""

    run: str
    category: str
    section: str
    issue: str
    severity: str
    confidence: str

    @property
    def section_key(self) -> str:
        """Blocking key: the section number, or the section text when there is none."""
        number = extract_section_number(self.section)
        return (
            number if number is not None else f"text:{self.section.casefold().strip()}"
        )


@dataclass
class Cluster:
    """A group of findings judged to be the same issue, drawn from one or more runs."""

    section_key: str
    members: list[Finding]

    @property
    def runs(self) -> set[str]:
        return {member.run for member in self.members}

    @property
    def support(self) -> int:
        """Number of distinct runs this issue appears in."""
        return len(self.runs)

    @property
    def categories(self) -> list[str]:
        """Sorted unique categories across all members."""
        return sorted({member.category for member in self.members})

    @property
    def confidences(self) -> list[str]:
        """Sorted unique confidence levels across all members."""
        return sorted({member.confidence for member in self.members})

    def representative(self) -> Finding:
        """The medoid member — the issue text most typical of the cluster."""
        if len(self.members) == 1:
            return self.members[0]
        return max(
            self.members,
            key=lambda m: sum(
                issue_jaccard(m.issue, other.issue)
                for other in self.members
                if other is not m
            ),
        )


@dataclass
class StabilityReport:
    """The computed stability of a set of runs."""

    runs: list[str]
    findings_per_run: dict[str, int]
    clusters: list[Cluster]
    excluded: set[str] = field(default_factory=set)
    judge_usage: JudgeUsage | None = None


def parse_excluded_categories(raw: str | None) -> set[str]:
    """Validate a comma-separated category list against ``FindingCategory``."""
    if not raw:
        return set()
    valid = {category.value for category in FindingCategory}
    requested = {part.strip() for part in raw.split(",") if part.strip()}
    unknown = requested - valid
    if unknown:
        message = (
            f"unknown categories {sorted(unknown)}; "
            f"valid categories are {sorted(valid)}"
        )
        raise SystemExit(message)
    return requested


def load_findings(path: Path, excluded: set[str]) -> list[Finding]:
    """Load one run's findings, dropping excluded categories."""
    data = json.loads(path.read_text(encoding="utf-8"))
    run = run_label(path)
    return [
        Finding(
            run=run,
            category=str(finding.get("category", "")),
            section=str(finding.get("section", "")),
            issue=str(finding.get("issue", "")),
            severity=str(finding.get("severity", "")),
            confidence=str(finding.get("confidence", "")),
        )
        for finding in data.get("findings", [])
        if finding.get("category") not in excluded
    ]


async def build_clusters(
    findings: list[Finding],
    judge: JudgeFn,
    low: float = DEFAULT_LOW_JACCARD,
    high: float = DEFAULT_HIGH_JACCARD,
    threshold: float = DEFAULT_THRESHOLD,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_section: SectionProgress | None = None,
) -> list[Cluster]:
    """Cluster findings into distinct issues, blocking by section number first.

    ``concurrency`` bounds how many judge calls run at once within a block.
    ``on_section`` (if given) is called as each section block begins, the natural
    unit of progress: the block count is known up front, before any judging.
    """
    bounded = bounded_judge(judge, concurrency)
    blocks: dict[str, list[Finding]] = {}
    for finding in findings:
        blocks.setdefault(finding.section_key, []).append(finding)

    clusters: list[Cluster] = []
    for number, (section_key, block) in enumerate(blocks.items(), start=1):
        if on_section is not None:
            on_section(number, len(blocks), section_key, len(block))
        pairs = list(combinations(range(len(block)), 2))
        edges = await score_edges(
            [finding.issue for finding in block], pairs, bounded, low, high, threshold
        )
        for component in connected_components(len(block), edges):
            clusters.append(Cluster(section_key, [block[index] for index in component]))
    return clusters


def _section_sort_key(section_key: str) -> tuple[int, str, tuple[int, ...]]:
    """Order section keys naturally: text-keyed first, then numbers numerically.

    Numbered keys are dotted decimals, so '10' sorts after '2' (not lexically); a
    uniformly typed tuple keeps numbered and text-keyed sections comparable.
    """
    if section_key.startswith("text:"):
        return (0, section_key.removeprefix("text:"), ())
    return (1, "", dotted_tuple(section_key))


def print_report(report: StabilityReport) -> None:
    """Print the aggregate stability and the per-finding consistency table."""
    n_runs = len(report.runs)
    print(f"Runs: {n_runs}")
    for run in report.runs:
        print(dim(f"  {run}: {report.findings_per_run[run]} findings"))
    if report.excluded:
        print(dim(f"Excluded categories: {', '.join(sorted(report.excluded))}"))

    agreements = list(pairwise_agreements(report.runs, list(report.clusters)).values())
    mean = sum(agreements) / len(agreements) if agreements else 1.0
    worst = min(agreements) if agreements else 1.0
    # Population SD: the run pairs are the whole set being summarised, not a sample.
    spread = statistics.pstdev(agreements) if len(agreements) > 1 else 0.0
    print(
        f"\nPairwise agreement (soft Dice over {len(agreements)} run pairs): "
        f"mean {mean:.3f}   sd {spread:.3f}   min {worst:.3f}"
    )

    histogram = support_histogram(list(report.clusters), n_runs)
    total = len(report.clusters)
    stable = histogram.get(n_runs, 0)
    print(f"Distinct issues: {total}   in all {n_runs} runs: {stable}")
    for support in range(n_runs, 0, -1):
        print(dim(f"  in {support}/{n_runs} runs: {histogram.get(support, 0)}"))

    if report.judge_usage is not None:
        usage = report.judge_usage
        print(
            dim(
                f"Judge tokens over {usage.calls} calls: "
                f"{usage.input_tokens} in, {usage.output_tokens} out "
                f"({usage.input_tokens + usage.output_tokens} total)"
            )
        )

    print("\nPer-issue consistency (most stable first):")
    ordered = sorted(
        report.clusters,
        key=lambda c: (-c.support, _section_sort_key(c.section_key)),
    )
    for cluster in ordered:
        representative = cluster.representative()
        ratio = support_colour(cluster.support, n_runs, f"{cluster.support}/{n_runs}")
        cats = ", ".join(cluster.categories)
        confs = ", ".join(cluster.confidences)
        print(
            f"  {ratio}  §{cluster.section_key}  [{cats}]  [{confs}]  {truncate(representative.issue)}"
        )


# Trailing "-publishing-<batch timestamp>-runNN" tail that generate_runs appends to
# the input stem (the checker infix is optional so pre-infix captures still work);
# stripped to recover the original document name for the report filename.
_RUN_FILE_SUFFIX = re.compile(r"(?:-publishing)?-\d{8}T\d{6}Z-run\d+$")


def section_display(section_key: str) -> str:
    """A section key for display: the section text for text-keyed blocks, else the number."""
    return section_key.removeprefix("text:")


def match_report_rows(report: StabilityReport) -> list[tuple[str, float, list[str]]]:
    """One row per cluster: section, match fraction, then each run's issue text.

    Clusters are laid out in section order so each row reads as one issue with every
    run's wording of it side by side; a run that did not raise the issue leaves a
    blank cell. A run contributing two findings to one cluster has them joined
    with ' | '.
    """
    n_runs = len(report.runs)
    ordered = sorted(
        report.clusters,
        key=lambda c: (_section_sort_key(c.section_key), -c.support),
    )
    rows: list[tuple[str, float, list[str]]] = []
    for cluster in ordered:
        by_run: dict[str, list[str]] = {}
        for member in cluster.members:
            by_run.setdefault(member.run, []).append(member.issue)
        cells = [" | ".join(dict.fromkeys(by_run.get(run, []))) for run in report.runs]
        rows.append(
            (
                section_display(cluster.section_key),
                cluster.support / n_runs if n_runs else 0.0,
                cells,
            )
        )
    return rows


def write_match_report(report: StabilityReport, path: Path) -> None:
    """Write the per-run match report to ``path`` as a formatted Excel workbook."""
    import openpyxl

    wb = openpyxl.Workbook()
    write_match_sheet(wb.active, report.runs, match_report_rows(report))
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
        help="Generate runs by analysing this .docx --runs times instead of passing "
        "RUN_FILEs (needs the publishing service running).",
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
            "Max analyses in flight when generating runs — distinct from "
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
        "--exclude-categories",
        default=None,
        help=(
            "Comma-separated finding categories to ignore, e.g. 'links'. "
            f"Valid: {', '.join(c.value for c in FindingCategory)}."
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
            "Max judge (LLM) calls in flight at once within a section block "
            f"(default: {DEFAULT_CONCURRENCY}; 1 = sequential)."
        ),
    )
    parser.add_argument(
        "--match-report",
        action="store_true",
        help=(
            "Also write a per-run match report Excel workbook "
            "(<input>-match-report-<ts>.xlsx) to --out-dir or the input's directory."
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
    if args.document is not None:
        paths = await _generate_run_files(args)
    else:
        paths = _existing_run_files(args)

    excluded = parse_excluded_categories(args.exclude_categories)
    per_run = {run_label(path): load_findings(path, excluded) for path in paths}
    findings = [finding for run in per_run.values() for finding in run]

    # Load the project .env (searching up from the cwd) before importing the model,
    # which reads Bedrock config from env at import time.
    load_dotenv(find_dotenv(usecwd=True))
    from app.infra.bedrock import llm

    judge, judge_usage = make_bedrock_judge(llm.claude_sonnet)

    def report_section(
        number: int, total: int, section_key: str, in_block: int
    ) -> None:
        progress(
            f"section {number}/{total}: §{section_key} ({in_block} findings) — "
            f"{judge_usage.calls} judge calls so far"
        )

    clusters = await build_clusters(
        findings,
        judge,
        args.low,
        args.high,
        args.threshold,
        concurrency=args.concurrency,
        on_section=report_section,
    )
    report = StabilityReport(
        runs=list(per_run),
        findings_per_run={run: len(items) for run, items in per_run.items()},
        clusters=clusters,
        excluded=excluded,
        judge_usage=judge_usage,
    )
    print_report(report)
    if args.match_report:
        path = match_report_path(args.document, args.out_dir, paths, _RUN_FILE_SUFFIX)
        write_match_report(report, path)
        progress(f"wrote match report {path}")


if __name__ == "__main__":
    asyncio.run(main())
