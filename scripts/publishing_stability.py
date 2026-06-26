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
  uv run --env-file .env python -m scripts.publishing_stability \
      run01.json run02.json run03.json [--exclude-categories links] \
      [--low F] [--high F] [--threshold F] [--concurrency N]

  # Or generate the runs first (needs the publishing service running):
  uv run --env-file .env python -m scripts.publishing_stability \
      --document input.docx --runs 10 [--run-concurrency N] [--concurrency N]

Each input is an analyse response (or captured run file) with a ``findings`` list.
Run generation and the stability comparison have separate concurrency controls:
``--run-concurrency`` bounds analyses in flight, ``--concurrency`` bounds judge calls.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

import pydantic
import pydantic_ai
from dotenv import find_dotenv, load_dotenv
from pydantic_ai.settings import ModelSettings

from app.publishing.models import FindingCategory
from scripts.publishing_common import extract_section_number, issue_jaccard
from scripts.publishing_runs import (
    DEFAULT_CONCURRENCY as DEFAULT_RUN_CONCURRENCY,
)
from scripts.publishing_runs import (
    DEFAULT_HOST,
    DEFAULT_UPLOADER,
    generate_runs,
)

# A judge: given two issue texts, score 0.0-1.0 how much they are the same problem.
JudgeFn = Callable[[str, str], Awaitable[float]]

# Progress callback: (block number, total blocks, section key, findings in block).
# Called as each section block starts — the natural, known-ahead unit of work.
SectionProgress = Callable[[int, int, str, int], None]

# Tiering thresholds for the same-problem decision. Above HIGH the lexical overlap
# is decisive (match without judging); at or below LOW it is decisive the other way;
# the band between is sent to the judge. THRESHOLD is the same-problem cut-off applied
# to the resulting score when drawing a clustering edge.
DEFAULT_HIGH_JACCARD = 0.6
DEFAULT_LOW_JACCARD = 0.1
DEFAULT_THRESHOLD = 0.5
# Max judge (LLM) calls in flight at once. 1 = sequential. The unit bounded is the
# pairwise comparison; raising it parallelises the judging within each section block.
DEFAULT_CONCURRENCY = 1

# Judge-call resilience. Bedrock throttling (ThrottlingException) is retried by
# boto3 internally with backoff, so it surfaces as slow calls rather than errors;
# we both retry exhausted throttles ourselves and warn on either signal so a long
# run's slowness is attributable to rate limiting rather than guessed at.
SLOW_CALL_WARN_S = 12.0
MAX_JUDGE_ATTEMPTS = 6
# Base of the exponential backoff between throttle retries: 5, 10, 20, 40, 80s.
THROTTLE_BACKOFF_S = 5.0
_THROTTLE_MARKERS = (
    "throttl",
    "too many requests",
    "toomanyrequests",
    "rate exceeded",
    "slowdown",
    "429",
)

JUDGE_SYSTEM_PROMPT = (
    "You compare two issue descriptions, each written by an automated document "
    "checker on a separate run over the same document. They are peers — neither is "
    "reference, and their A/B order is arbitrary. Respond with a JSON object "
    "{reason, score}: score 1.0 if they identify the same underlying problem, 0.0 "
    "if unrelated, intermediate for partial overlap. Judge the substance of the "
    "problem, not the wording."
)


class SameProblemGrade(pydantic.BaseModel):
    """The judge's verdict on whether two issue descriptions are the same problem."""

    reason: str
    score: float


# Own judge agent (the library's judges discard usage; this keeps the run result so
# tokens can be counted). The Bedrock model is supplied per run, as the checker does.
_judge_agent = pydantic_ai.Agent(
    output_type=SameProblemGrade,
    system_prompt=JUDGE_SYSTEM_PROMPT,
)


@dataclass
class JudgeUsage:
    """Running token totals across the judge calls made during a comparison."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


_COLOUR: bool = False


def _wrap(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def _support_colour(support: int, n_runs: int, text: str) -> str:
    """Green when in every run, red when in only one, yellow in between."""
    if support <= 1:
        return _wrap("31", text)
    return _wrap("32", text) if support >= n_runs else _wrap("33", text)


def _dim(text: str) -> str:
    return _wrap("2", text)


def _warn(message: str) -> None:
    """Emit a diagnostic to stderr, kept off the stdout report."""
    print(f"[stability] {message}", file=sys.stderr, flush=True)


def _progress(message: str) -> None:
    """Emit a dim progress line to stderr, kept off the stdout report."""
    print(_dim(f"[stability] {message}"), file=sys.stderr, flush=True)


def _is_throttle(error: BaseException) -> bool:
    """Whether an exception looks like a rate-limit / throttling response."""
    text = f"{type(error).__name__} {error}".lower()
    return any(marker in text for marker in _THROTTLE_MARKERS)


@dataclass(frozen=True)
class Finding:
    """One finding from one run, reduced to the fields used for comparison."""

    run: str
    category: str
    section: str
    issue: str
    severity: str

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
        )
        for finding in data.get("findings", [])
        if finding.get("category") not in excluded
    ]


def run_label(path: Path) -> str:
    """A compact run name: the trailing ``runNN`` token if present, else the stem."""
    stem = path.stem
    _, marker, tail = stem.rpartition("run")
    if marker and tail.isdigit():
        return f"run{tail}"
    return stem


async def same_problem_score(
    first: str, second: str, judge: JudgeFn, low: float, high: float
) -> float:
    """Tiered 0.0-1.0 score that two issue texts are the same problem.

    Lexical Jaccard decides the clear cases (keeping the judge — itself a variance
    source — out of them); the judge is consulted only for the ambiguous band. The
    pair is canonicalised before judging so the score is symmetric by construction.
    """
    jaccard = issue_jaccard(first, second)
    if jaccard >= high:
        return 1.0
    if jaccard <= low:
        return 0.0
    canonical_first, canonical_second = sorted((first, second))
    return await judge(canonical_first, canonical_second)


def connected_components(size: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    """Group ``range(size)`` into connected components given undirected ``edges``."""
    parent = list(range(size))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for left, right in edges:
        parent[find(left)] = find(right)

    groups: dict[int, list[int]] = {}
    for node in range(size):
        groups.setdefault(find(node), []).append(node)
    return list(groups.values())


def _bounded_judge(judge: JudgeFn, concurrency: int) -> JudgeFn:
    """A judge that admits at most ``concurrency`` calls at once via a semaphore.

    Only the (slow) judge call is bounded; the Jaccard gate runs unbounded, so a slot
    is held for an actual LLM call, never for a pair the lexical gate settles.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded(first: str, second: str) -> float:
        async with semaphore:
            return await judge(first, second)

    return bounded


async def _block_edges(
    block: list[Finding], judge: JudgeFn, low: float, high: float, threshold: float
) -> list[tuple[int, int]]:
    """Same-problem edges among a section block's findings (indices into ``block``).

    A block's pairs are independent, so they are scored concurrently; how many judge
    calls actually overlap is bounded by the semaphore inside ``judge``.
    """
    pairs = list(combinations(range(len(block)), 2))
    scores = await asyncio.gather(
        *(
            same_problem_score(block[i].issue, block[j].issue, judge, low, high)
            for i, j in pairs
        )
    )
    return [
        pair for pair, score in zip(pairs, scores, strict=True) if score >= threshold
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
    bounded = _bounded_judge(judge, concurrency)
    blocks: dict[str, list[Finding]] = {}
    for finding in findings:
        blocks.setdefault(finding.section_key, []).append(finding)

    clusters: list[Cluster] = []
    for number, (section_key, block) in enumerate(blocks.items(), start=1):
        if on_section is not None:
            on_section(number, len(blocks), section_key, len(block))
        edges = await _block_edges(block, bounded, low, high, threshold)
        for component in connected_components(len(block), edges):
            clusters.append(Cluster(section_key, [block[index] for index in component]))
    return clusters


def pairwise_agreements(
    runs: list[str], clusters: list[Cluster]
) -> dict[tuple[str, str], float]:
    """Soft-Dice agreement for every unordered run pair, from the cluster supports.

    Each run is the set of issue-clusters it touched; agreement is
    ``2 * shared / (|i| + |j|)``. Symmetric, and 1.0 for two runs that both raised
    nothing.
    """
    touched = {run: sum(run in cluster.runs for cluster in clusters) for run in runs}
    agreements: dict[tuple[str, str], float] = {}
    for first, second in combinations(runs, 2):
        shared = sum(
            1
            for cluster in clusters
            if first in cluster.runs and second in cluster.runs
        )
        denominator = touched[first] + touched[second]
        agreements[first, second] = 2 * shared / denominator if denominator else 1.0
    return agreements


def support_histogram(clusters: list[Cluster], n_runs: int) -> dict[int, int]:
    """How many clusters have each support level 1..N."""
    histogram = dict.fromkeys(range(1, n_runs + 1), 0)
    for cluster in clusters:
        histogram[cluster.support] = histogram.get(cluster.support, 0) + 1
    return histogram


def _section_sort_key(section_key: str) -> tuple[int, str, tuple[int, ...]]:
    """Order section keys naturally: text-keyed first, then numbers numerically.

    Numbered keys are dotted decimals, so '10' sorts after '2' (not lexically); a
    uniformly typed tuple keeps numbered and text-keyed sections comparable.
    """
    if section_key.startswith("text:"):
        return (0, section_key.removeprefix("text:"), ())
    return (1, "", tuple(int(part) for part in section_key.split(".")))


def _truncate(text: str, width: int = 100) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= width else f"{collapsed[: width - 1]}…"


def print_report(report: StabilityReport) -> None:
    """Print the aggregate stability and the per-finding consistency table."""
    n_runs = len(report.runs)
    print(f"Runs: {n_runs}")
    for run in report.runs:
        print(_dim(f"  {run}: {report.findings_per_run[run]} findings"))
    if report.excluded:
        print(_dim(f"Excluded categories: {', '.join(sorted(report.excluded))}"))

    agreements = list(pairwise_agreements(report.runs, report.clusters).values())
    mean = sum(agreements) / len(agreements) if agreements else 1.0
    worst = min(agreements) if agreements else 1.0
    # Population SD: the run pairs are the whole set being summarised, not a sample.
    spread = statistics.pstdev(agreements) if len(agreements) > 1 else 0.0
    print(
        f"\nPairwise agreement (soft Dice over {len(agreements)} run pairs): "
        f"mean {mean:.3f}   sd {spread:.3f}   min {worst:.3f}"
    )

    histogram = support_histogram(report.clusters, n_runs)
    total = len(report.clusters)
    stable = histogram.get(n_runs, 0)
    print(f"Distinct issues: {total}   in all {n_runs} runs: {stable}")
    for support in range(n_runs, 0, -1):
        print(_dim(f"  in {support}/{n_runs} runs: {histogram.get(support, 0)}"))

    if report.judge_usage is not None:
        usage = report.judge_usage
        print(
            _dim(
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
        ratio = _support_colour(cluster.support, n_runs, f"{cluster.support}/{n_runs}")
        print(f"  {ratio}  §{cluster.section_key}  {_truncate(representative.issue)}")


def make_bedrock_judge(model: Any) -> tuple[JudgeFn, JudgeUsage]:
    """A Bedrock-backed judge (temperature 0) and the usage it accumulates.

    Each call's input/output tokens are summed into the returned ``JudgeUsage`` so
    the cost of the similarity checks can be reported. Only middle-band pairs reach
    here; Jaccard-resolved pairs cost nothing.
    """
    usage = JudgeUsage()

    async def run_once(prompt: str) -> tuple[Any, float]:
        """Run the judge once, retrying explicit throttles with backoff."""
        for attempt in range(1, MAX_JUDGE_ATTEMPTS + 1):
            start = time.perf_counter()
            try:
                result = await _judge_agent.run(
                    prompt, model=model, model_settings=ModelSettings(temperature=0.0)
                )
            except Exception as error:  # noqa: BLE001 — classify, warn, then re-raise
                if not _is_throttle(error):
                    raise
                if attempt == MAX_JUDGE_ATTEMPTS:
                    _warn(
                        f"still rate limited after {MAX_JUDGE_ATTEMPTS} attempts on "
                        f"judge call {usage.calls + 1}; giving up"
                    )
                    raise
                delay = THROTTLE_BACKOFF_S * 2 ** (attempt - 1)
                _warn(
                    f"rate limited on judge call {usage.calls + 1} "
                    f"(attempt {attempt}/{MAX_JUDGE_ATTEMPTS}); backing off {delay:.0f}s"
                )
                await asyncio.sleep(delay)
            else:
                return result, time.perf_counter() - start
        msg = "unreachable: judge retry loop exhausted without return or raise"
        raise RuntimeError(msg)

    async def judge(first: str, second: str) -> float:
        prompt = f"<IssueA>\n{first}\n</IssueA>\n<IssueB>\n{second}\n</IssueB>"
        result, elapsed = await run_once(prompt)
        run_usage = result.usage
        usage.calls += 1
        usage.input_tokens += run_usage.input_tokens or 0
        usage.output_tokens += run_usage.output_tokens or 0
        # A slow call with no exception means boto3 is silently retrying a throttle.
        if elapsed > SLOW_CALL_WARN_S:
            _warn(
                f"judge call {usage.calls} took {elapsed:.0f}s — likely being "
                "throttled (boto3 is backing off internally)"
            )
        return result.output.score

    return judge, usage


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
        help="Number of runs to generate from --document (two or more).",
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
    if args.runs is None or args.runs < 2:
        message = "--runs must be at least 2 when generating from --document"
        raise SystemExit(message)
    if args.run_concurrency < 1:
        message = "--run-concurrency must be at least 1"
        raise SystemExit(message)
    document = Path(args.document)
    out_dir = Path(args.out_dir) if args.out_dir else document.resolve().parent
    _progress(
        f"generating {args.runs} runs from {document.name} "
        f"(run-concurrency {args.run_concurrency})"
    )
    return await generate_runs(
        document,
        runs=args.runs,
        concurrency=args.run_concurrency,
        host=args.host,
        uploader=args.uploader,
        out_dir=out_dir,
        on_run=lambda done, total: _progress(f"generated run {done}/{total}"),
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
    global _COLOUR  # noqa: PLW0603
    args = parse_args()
    _COLOUR = sys.stdout.isatty() and not args.no_colour

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
        _progress(
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


if __name__ == "__main__":
    asyncio.run(main())
