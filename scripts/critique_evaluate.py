#!/usr/bin/env python3
"""Evaluate the critique checker against a ground-truth expectations file.

Drives the live async flow as a black-box client (the same public HTTP contract the
frontend uses, no app internals): upload the .docx once (reusing an already-parsed
copy with a matching content hash if present), then for each run submit it for
critique (``POST /critique/jobs``) and poll the job to completion. Every expected
finding is then checked against the run's produced findings for a gate-passing
match, whose problem description (``what``) is scored for correctness with the
pydantic-evals LLM judge.

The critique response carries one report per standard (``gds`` / ``defra_style``)
and expectations are keyed the same way. Standards are a hard partition: an
expectation is only ever compared with findings from its own standard's report --
matching, ranking, judging and near-miss diagnostics never cross reports.

A finding's location is its ``where`` field, which may name several sections
(e.g. "Sections 3.2 and 5.1"). Each produced finding is expanded into one
candidate per section number in its ``where`` before matching. A candidate clears
the mechanical gates for an expectation when:
  * the expectation's single section number equals the candidate's section number
    (expectations must contain exactly one; they are rejected up front otherwise);
  * severity is at least the expected level (low < medium < high < critical).
Among the gate-passers, the one whose ``what`` terms are most similar (jaccard,
with hyperlinks kept whole) is the match, and only that one is judged for
correctness (0.0-1.0) by the LLM. Only the standard, where, severity and what
are used.

Usage:
  uv run scripts/critique_evaluate.py \
      <document.docx> <critique-expectations.json> \
      [--runs N] [--host URL] [--uploader URL] [--out-dir DIR]

Expectations file shape (findings keyed by standard, each a subset of that
report's findings; either standard may be omitted):
  {"findings": {"defra_style": [{"where": ..., "severity": ..., "what": ...}],
                "gds": [...]}}

Each run's critique result is written to ``<doc-stem>-critique-<batch-utc>-run<NN>.json``
in the output directory (default: the document file's directory). ``<batch-utc>`` is one
ISO-8601-basic UTC timestamp captured when the script starts, shared by every file
of the invocation -- so a batch's files share a common prefix, never overwrite a
prior batch, and sort chronologically.
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import find_dotenv, load_dotenv
from pydantic_ai.settings import ModelSettings
from pydantic_evals.evaluators.llm_as_a_judge import judge_output_expected

from scripts.console import (
    colour_ratio,
    colour_score,
    dim,
    green,
    red,
    set_colour,
)
from scripts.publishing_common import (
    extract_section_number,
    extract_section_numbers,
    issue_jaccard,
    severity_rank,
)
from scripts.publishing_runs import (
    CRITIQUE_JOBS_PATH,
    CRITIQUE_TIMEOUT_S,
    DEFAULT_CONCURRENCY,
    DEFAULT_HOST,
    DEFAULT_UPLOADER,
    REQUEST_TIMEOUT_S,
    analyse_document,
    capture_name,
    resolve_document_id,
    validate_document,
)

STANDARDS = ("gds", "defra_style")

WHAT_RUBRIC = (
    "EXPECTED is a style/content divergence identified by a human reviewer. "
    "OUTPUT is the problem description produced by an automated style critic. "
    "Score how fully OUTPUT identifies the same underlying problem as EXPECTED: "
    "1.0 = the same problem, clearly captured; 0.0 = unrelated or missed. Judge "
    "the substance of the problem, not the wording."
)


def load_expectations(path: Path) -> list[dict[str, Any]]:
    """Load and flatten the per-standard expectations, validating up front.

    Returns one expectation dict per entry with its ``standard`` attached. Either
    standard key may be omitted; unknown keys are rejected, as is any expectation
    whose ``where`` does not name exactly one section number (split multi-section
    ground truth into separate entries by hand).
    """
    by_standard = json.loads(path.read_text(encoding="utf-8"))["findings"]
    unknown = sorted(set(by_standard) - set(STANDARDS))
    if unknown:
        message = f"unknown standard keys {unknown}; expected keys from {STANDARDS}"
        raise SystemExit(message)
    expectations = [
        {**expected, "standard": standard}
        for standard in STANDARDS
        for expected in by_standard.get(standard, [])
    ]
    bad_where = [
        f"{expected['standard']} #{index + 1}"
        for index, expected in enumerate(expectations)
        if len(extract_section_numbers(str(expected.get("where", "")))) != 1
    ]
    if bad_where:
        message = (
            f"expectations [{', '.join(bad_where)}] must name exactly one section "
            "number in 'where'; split multi-section expectations into one entry each"
        )
        raise SystemExit(message)
    return expectations


def candidate_pools(reports: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Per-standard candidate findings, one candidate per section number in ``where``.

    The per-report split is the standards partition: an expectation only ever sees
    its own standard's pool. A finding whose ``where`` names no section number
    yields one number-less candidate -- it can never pass the section gate but
    remains visible to near-miss diagnostics.
    """
    pools: dict[str, list[dict[str, Any]]] = {standard: [] for standard in STANDARDS}
    for report in reports:
        pool = pools.setdefault(str(report.get("standard", "")), [])
        for finding in report.get("findings", []):
            numbers = extract_section_numbers(str(finding.get("where", "")))
            pool.extend(
                {**finding, "section_number": number} for number in numbers or [None]
            )
    return pools


def gate_breakdown(
    expected: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, bool]:
    """Per-gate pass/fail of a produced candidate against an expectation."""
    return {
        "severity": severity_rank(str(candidate.get("severity", "")))
        >= severity_rank(str(expected.get("severity", ""))),
        "section": extract_section_number(str(expected.get("where", "")))
        == candidate.get("section_number"),
    }


def best_near_miss(
    expected: dict[str, Any], pool: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    """The most diagnostic near-miss candidate and its gate outcomes.

    Prefers a candidate in the right section, then one passing the most gates, so
    the breakdown explains the closest the checker came to the expectation.
    """
    best: dict[str, Any] | None = None
    best_gates: dict[str, bool] = {}
    best_rank: tuple[bool, int] | None = None
    for candidate in pool:
        gates = gate_breakdown(expected, candidate)
        rank = (gates["section"], sum(gates.values()))
        if best_rank is None or rank > best_rank:
            best, best_gates, best_rank = candidate, gates, rank
    return best, best_gates


def describe_gates(
    expected: dict[str, Any], candidate: dict[str, Any], gates: dict[str, bool]
) -> str:
    """Explain which gate the near-miss failed, stating the failing produced value."""
    tick = green("✓")
    cross = red("✗")
    if gates["severity"]:
        severity_part = f"severity {tick}"
    else:
        severity_part = (
            f"severity {cross} (got {candidate.get('severity', '?')}, "
            f"need ≥{expected.get('severity', '?')})"
        )
    if gates["section"]:
        section_part = f"section {tick}"
    else:
        produced_where = str(candidate.get("where", "")).strip()
        got = repr(produced_where) if produced_where else "none"
        expected_where = str(expected.get("where", "")).strip()
        section_part = f"section {cross} (got {got}, need {expected_where!r})"
    return f"{severity_part}  {section_part}"


async def what_correctness(
    expected_what: str, produced_what: str, model: Any
) -> tuple[float, str]:
    """Judge how well a produced problem description captures the expected one."""
    grading = await judge_output_expected(
        output=produced_what,
        expected_output=expected_what,
        rubric=WHAT_RUBRIC,
        model=model,
        model_settings=ModelSettings(temperature=0.0),
    )
    return grading.score, grading.reason


@dataclass
class ExpectationOutcome:
    """The result of matching one expectation against its standard's candidates."""

    matched: bool
    score: float | None
    reason: str
    finding: dict[str, Any] | None
    gates: dict[str, bool]
    what_jaccard: float


async def evaluate_expectation(
    expected: dict[str, Any], pool: list[dict[str, Any]], model: Any
) -> ExpectationOutcome:
    """Match an expectation via a section-first waterfall, then judge the best candidate.

    ``pool`` holds only the expectation's own standard's candidates -- the standards
    partition happens before this function.
    Stage 1 — section: narrows to candidates in the expected section.
    Stage 2 — severity: narrows to candidates at or above the expected severity.
    The survivors are ranked by jaccard on ``what`` terms; only the top-ranked is
    judged. Each stage reports a miss with the best near-miss from that stage.
    """
    expected_what = str(expected.get("what", ""))

    def by_what_jaccard(candidate: dict[str, Any]) -> float:
        return issue_jaccard(expected_what, str(candidate.get("what", "")))

    in_section = [c for c in pool if gate_breakdown(expected, c)["section"]]
    if not in_section:
        finding, gates = best_near_miss(expected, pool)
        return ExpectationOutcome(False, None, "", finding, gates, 0.0)

    candidates = [c for c in in_section if gate_breakdown(expected, c)["severity"]]
    if not candidates:
        finding = max(
            in_section, key=lambda c: severity_rank(str(c.get("severity", "")))
        )
        return ExpectationOutcome(
            False, None, "", finding, gate_breakdown(expected, finding), 0.0
        )

    best = max(candidates, key=by_what_jaccard)
    jaccard = issue_jaccard(expected_what, str(best.get("what", "")))
    score, reason = await what_correctness(
        expected_what, str(best.get("what", "")), model
    )
    return ExpectationOutcome(
        True, score, reason, best, gate_breakdown(expected, best), jaccard
    )


def expectation_label(expected: dict[str, Any], index: int) -> str:
    """A compact, content-light label for an expectation."""
    number = extract_section_number(str(expected.get("where", ""))) or "?"
    standard = expected.get("standard", "?")
    severity = expected.get("severity", "?")
    return f"#{index + 1} {standard} ≥{severity} §{number}"


def print_match_detail(
    index: int, expected: dict[str, Any], outcome: ExpectationOutcome
) -> None:
    """Print the matched finding and the judge's rationale (shows document content)."""
    finding = outcome.finding or {}
    score = outcome.score or 0.0
    print(
        f"    {expectation_label(expected, index)}  "
        f"{colour_score(score, f'score {score:.3f}')}  jaccard {outcome.what_jaccard:.3f}"
    )
    print(f"      matched where:  {finding.get('where', '')}")
    print(f"      produced what:  {finding.get('what', '')}")
    print(f"      judge:          {outcome.reason}")


def print_miss_detail(
    index: int,
    expected: dict[str, Any],
    finding: dict[str, Any] | None,
    gates: dict[str, bool],
) -> None:
    """Print why an expectation did not match (which gate the near-miss failed)."""
    label = expectation_label(expected, index)
    miss = red("miss")
    if finding is None:
        print(f"  {miss} {label}: no {expected.get('standard', '?')} findings produced")
        return
    print(f"  {miss} {label}: {describe_gates(expected, finding, gates)}")


@dataclass
class Batch:
    """Shared, read-only context every run in one invocation needs."""

    client: httpx.AsyncClient
    host: str
    document_id: str
    expectations: list[dict[str, Any]]
    model: Any
    out_dir: Path
    stem: str
    batch_ts: str
    run_width: int
    semaphore: asyncio.Semaphore


@dataclass
class RunResult:
    """One run's output location, per-expectation outcomes and work duration."""

    run_index: int
    out_path: Path
    outcomes: list[ExpectationOutcome]
    duration: float


async def run_and_evaluate(batch: Batch, run_index: int) -> RunResult:
    """Critique the document once, capture the response, evaluate every expectation.

    The semaphore bounds how many runs are in flight; the duration measures only the
    work, not time spent waiting for a slot.
    """
    async with batch.semaphore:
        start = time.perf_counter()
        data = await analyse_document(
            batch.client,
            batch.host,
            batch.document_id,
            submit_path=CRITIQUE_JOBS_PATH,
            jobs_path=CRITIQUE_JOBS_PATH,
            timeout_s=CRITIQUE_TIMEOUT_S,
        )
        name = capture_name(
            batch.stem, "critique", batch.batch_ts, run_index, batch.run_width
        )
        out_path = batch.out_dir / name
        out_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        pools = candidate_pools(data.get("reports", []))
        outcomes = [
            await evaluate_expectation(
                expected, pools.get(str(expected["standard"]), []), batch.model
            )
            for expected in batch.expectations
        ]
        return RunResult(run_index, out_path, outcomes, time.perf_counter() - start)


def print_run_progress(
    result: RunResult,
    completed: int,
    total_runs: int,
    expectations: list[dict[str, Any]],
    show_reasons: bool,
) -> None:
    """Print a run's result as it completes, with miss/reason detail per expectation."""
    matched = sum(1 for outcome in result.outcomes if outcome.matched)
    total = len(expectations)
    scores = [
        outcome.score
        for outcome in result.outcomes
        if outcome.matched and outcome.score is not None
    ]
    mean = sum(scores) / len(scores) if scores else 0.0
    match_text = colour_ratio(matched, total, f"matches {matched}/{total}")
    score_text = (
        colour_score(mean, f"mean correctness {mean:.3f}")
        if scores
        else dim(f"mean correctness {mean:.3f}")
    )
    print(
        f"{dim(f'[{completed}/{total_runs}]')} {result.out_path.name}   "
        f"{match_text}   {score_text}   {dim(f'{result.duration:.1f}s')}"
    )
    for index, (expected, outcome) in enumerate(
        zip(expectations, result.outcomes, strict=True)
    ):
        if not outcome.matched:
            print_miss_detail(index, expected, outcome.finding, outcome.gates)
        elif show_reasons and outcome.finding is not None:
            print_match_detail(index, expected, outcome)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("document", help="Path to the guidance document (.docx).")
    parser.add_argument(
        "expectations", help="Path to the ground-truth expectations JSON file."
    )
    parser.add_argument(
        "--runs", type=int, default=5, help="Number of critique runs (default: 5)."
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Max runs in flight at once (default: {DEFAULT_CONCURRENCY}).",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Guidance backend base URL (default: {DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--uploader",
        default=DEFAULT_UPLOADER,
        help=f"CDP uploader base URL (default: {DEFAULT_UPLOADER}).",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for captured run files (default: the document file's directory).",
    )
    parser.add_argument(
        "--show-reasons",
        action="store_true",
        help="Print the judge's rationale and matched finding for each match.",
    )
    parser.add_argument(
        "--no-colour",
        action="store_true",
        help="Disable ANSI colour (default: on when stdout is a TTY).",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    set_colour(sys.stdout.isatty() and not args.no_colour)
    if args.runs < 1:
        message = "--runs must be at least 1"
        raise SystemExit(message)

    document_path = Path(args.document)
    validate_document(document_path)
    expectations = load_expectations(Path(args.expectations))
    out_dir = Path(args.out_dir) if args.out_dir else document_path.resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = document_path.stem
    # One timestamp per invocation: the shared per-batch prefix that de-conflicts
    # this batch's files from any other run's.
    batch_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_width = max(2, len(str(args.runs)))

    # Load the project .env (searching up from the cwd) before importing the model,
    # which reads Bedrock config from env at import time -- so this runs from any cwd
    # without an explicit --env-file.
    load_dotenv(find_dotenv(usecwd=True))
    from app.infra.bedrock import llm

    model = llm.claude_sonnet

    total = len(expectations)
    concurrency = max(1, min(args.concurrency, args.runs))

    print(
        f"Document: {document_path.name}   Expectations: {total}   "
        f"Runs: {args.runs}   Concurrency: {concurrency}   Host: {args.host}"
    )

    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        document_id = await resolve_document_id(
            client, args.host, args.uploader, document_path
        )
        print(f"Document id: {document_id}")
        batch = Batch(
            client=client,
            host=args.host,
            document_id=document_id,
            expectations=expectations,
            model=model,
            out_dir=out_dir,
            stem=stem,
            batch_ts=batch_ts,
            run_width=run_width,
            semaphore=asyncio.Semaphore(concurrency),
        )
        tasks = [
            asyncio.create_task(run_and_evaluate(batch, run_no))
            for run_no in range(1, args.runs + 1)
        ]
        results: list[RunResult] = []
        for completed, finished in enumerate(asyncio.as_completed(tasks), start=1):
            result = await finished
            results.append(result)
            print_run_progress(
                result, completed, args.runs, expectations, args.show_reasons
            )
    elapsed = time.perf_counter() - started

    match_counts = [0] * total
    score_sums = [0.0] * total
    score_counts = [0] * total
    for result in results:
        for index, outcome in enumerate(result.outcomes):
            if not outcome.matched:
                continue
            match_counts[index] += 1
            if outcome.score is not None:
                score_sums[index] += outcome.score
                score_counts[index] += 1

    print("\nPer-expectation match rate across runs:")
    for index, expected in enumerate(expectations):
        mean = score_sums[index] / score_counts[index] if score_counts[index] else 0.0
        matched = match_counts[index]
        match_text = colour_ratio(matched, args.runs, f"matched {matched}/{args.runs}")
        score_text = (
            colour_score(mean, f"mean correctness {mean:.3f}")
            if score_counts[index]
            else dim(f"mean correctness {mean:.3f}")
        )
        print(f"  {expectation_label(expected, index)}: {match_text}   {score_text}")

    judged = sum(score_counts)
    overall_mean = sum(score_sums) / judged if judged else 0.0
    mean_matches = sum(match_counts) / args.runs
    mean_per_run = sum(r.duration for r in results) / len(results) if results else 0.0
    mm_text = colour_ratio(
        int(mean_matches), total, f"mean matches {mean_matches:.2f}/{total}"
    )
    mc_text = (
        colour_score(overall_mean, f"mean what correctness {overall_mean:.3f}")
        if judged
        else dim(f"mean what correctness {overall_mean:.3f}")
    )
    print(f"\nOverall: {mm_text}   {mc_text}")
    print(
        dim(
            f"Timing: elapsed {elapsed:.1f}s   mean per run {mean_per_run:.1f}s   "
            f"({args.runs} runs, concurrency {concurrency})"
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
