#!/usr/bin/env python3
"""Evaluate the publishing checker against a ground-truth expectations file.

Drives the live async flow as a black-box client (the same public HTTP contract
``scripts/publishing.sh`` uses, no app internals): upload the .docx once (reusing
an already-parsed copy with a matching content hash if present), then for each run
submit it for analysis and poll the job to completion. Every expected finding is
then checked against the run's produced findings for a gate-passing match, whose
issue text is scored for correctness with the pydantic-evals LLM judge.

A produced finding clears the mechanical gates for an expectation when:
  * the expected section number appears in the produced section (titles ignored;
    expectations without a section number are rejected up front);
  * category is an exact match;
  * severity is at least the expected level (info < low < medium < high < critical).
Among the gate-passers, the one whose issue terms are most similar (jaccard, with
hyperlinks kept whole) is the match, and only that one is judged for issue
correctness (0.0-1.0) by the LLM. Only section, category, severity and issue are used.

Usage:
  uv run scripts/publishing_evaluate.py \
      <document.docx> <expectations.json> \
      [--runs N] [--host URL] [--uploader URL] [--out-dir DIR]

Expectations file shape (a subset of the analyse response):
  {"findings": [{"category": ..., "section": ..., "severity": ..., "issue": ...}]}

Each run's analysis result is written to ``<doc-stem>-<batch-utc>-run<NN>.json`` in
the output directory (default: the document file's directory). ``<batch-utc>`` is one
ISO-8601-basic UTC timestamp captured when the script starts, shared by every file
of the invocation -- so a batch's files share a common prefix, never overwrite a
prior batch, and sort chronologically.
"""

import argparse
import asyncio
import json
import re
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

from scripts.publishing_common import (
    extract_section_number,
    issue_jaccard,
    severity_rank,
)
from scripts.publishing_runs import (
    DEFAULT_CONCURRENCY,
    DEFAULT_HOST,
    DEFAULT_UPLOADER,
    REQUEST_TIMEOUT_S,
    analyse_document,
    resolve_document_id,
    validate_document,
)

_COLOUR: bool = False


def _wrap(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def _green(s: str) -> str:
    return _wrap("32", s)


def _yellow(s: str) -> str:
    return _wrap("33", s)


def _red(s: str) -> str:
    return _wrap("31", s)


def _dim(s: str) -> str:
    return _wrap("2", s)


def _colour_ratio(matched: int, total: int, text: str) -> str:
    if matched == 0:
        return _red(text)
    return _green(text) if matched >= total else _yellow(text)


def _colour_score(score: float, text: str) -> str:
    if score >= 0.8:
        return _green(text)
    return _yellow(text) if score >= 0.4 else _red(text)


ISSUE_RUBRIC = (
    "EXPECTED is a document-quality issue identified by a human reviewer. "
    "OUTPUT is the issue text produced by an automated checker. Score how fully "
    "OUTPUT identifies the same underlying problem as EXPECTED: 1.0 = the same "
    "problem, clearly captured; 0.0 = unrelated or missed. Judge the substance "
    "of the problem, not the wording."
)


def section_number_match(expected_section: str, produced_section: str) -> bool:
    """Whether the produced section contains the expectation's section number.

    Section matching is purely mechanical on the number -- titles are ignored.
    Expectations without a number are rejected up front (see ``main``).
    """
    number = extract_section_number(expected_section)
    if number is None:
        return False
    pattern = rf"(?<!\d){re.escape(number)}(?!\d)"
    return re.search(pattern, produced_section) is not None


def gate_breakdown(
    expected: dict[str, Any], produced: dict[str, Any]
) -> dict[str, bool]:
    """Per-gate pass/fail of a produced finding against an expectation."""
    return {
        "category": expected.get("category") == produced.get("category"),
        "severity": severity_rank(str(produced.get("severity", "")))
        >= severity_rank(str(expected.get("severity", ""))),
        "section": section_number_match(
            str(expected.get("section", "")), str(produced.get("section", ""))
        ),
    }


def best_near_miss(
    expected: dict[str, Any], produced: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, bool]]:
    """The most diagnostic near-miss finding and its gate outcomes.

    Prefers a finding in the right section, then one passing the most gates, so
    the breakdown explains the closest the checker came to the expectation.
    """
    best: dict[str, Any] | None = None
    best_gates: dict[str, bool] = {}
    best_rank: tuple[bool, int] | None = None
    for finding in produced:
        gates = gate_breakdown(expected, finding)
        rank = (gates["section"], sum(gates.values()))
        if best_rank is None or rank > best_rank:
            best, best_gates, best_rank = finding, gates, rank
    return best, best_gates


def describe_gates(
    expected: dict[str, Any], finding: dict[str, Any], gates: dict[str, bool]
) -> str:
    """Explain which gate the near-miss failed, stating the failing produced value."""
    tick = _green("✓")
    cross = _red("✗")
    if not gates["category"]:
        return f"category {cross} (no {expected.get('category', '?')} finding produced)"
    if gates["severity"]:
        severity_part = f"severity {tick}"
    else:
        severity_part = (
            f"severity {cross} (got {finding.get('severity', '?')}, "
            f"need ≥{expected.get('severity', '?')})"
        )
    if gates["section"]:
        section_part = f"section {tick}"
    else:
        produced_section = str(finding.get("section", "")).strip()
        got = repr(produced_section) if produced_section else "none"
        expected_section = str(expected.get("section", "")).strip()
        section_part = f"section {cross} (got {got}, need {expected_section!r})"
    return f"category {tick}  {severity_part}  {section_part}"


async def issue_correctness(
    expected_issue: str, produced_issue: str, model: Any
) -> tuple[float, str]:
    """Judge how well a produced issue captures the expected issue: (score, reason)."""
    grading = await judge_output_expected(
        output=produced_issue,
        expected_output=expected_issue,
        rubric=ISSUE_RUBRIC,
        model=model,
        model_settings=ModelSettings(temperature=0.0),
    )
    return grading.score, grading.reason


@dataclass
class ExpectationOutcome:
    """The result of matching one expectation against a run's produced findings."""

    matched: bool
    score: float | None
    reason: str
    finding: dict[str, Any] | None
    gates: dict[str, bool]
    issue_jaccard: float


async def evaluate_expectation(
    expected: dict[str, Any], produced: list[dict[str, Any]], model: Any
) -> ExpectationOutcome:
    """Match an expectation via a section-first waterfall, then judge the best candidate.

    Stage 1 — section: narrows to findings in the expected section.
    Stage 2 — category: narrows to findings of the expected category within that section.
    Stage 3 — severity: narrows to findings at or above the expected severity.
    The survivors are ranked by jaccard on issue terms; only the top-ranked is judged.
    Each stage reports a miss with the best near-miss from that stage as context.
    """
    expected_issue = str(expected.get("issue", ""))

    def by_issue_jaccard(f: dict[str, Any]) -> float:
        return issue_jaccard(expected_issue, str(f.get("issue", "")))

    in_section = [f for f in produced if gate_breakdown(expected, f)["section"]]
    if not in_section:
        finding, gates = best_near_miss(expected, produced)
        return ExpectationOutcome(False, None, "", finding, gates, 0.0)

    right_category = [f for f in in_section if gate_breakdown(expected, f)["category"]]
    if not right_category:
        finding = max(in_section, key=by_issue_jaccard)
        return ExpectationOutcome(
            False, None, "", finding, gate_breakdown(expected, finding), 0.0
        )

    candidates = [f for f in right_category if gate_breakdown(expected, f)["severity"]]
    if not candidates:
        finding = max(
            right_category, key=lambda f: severity_rank(str(f.get("severity", "")))
        )
        return ExpectationOutcome(
            False, None, "", finding, gate_breakdown(expected, finding), 0.0
        )

    best = max(candidates, key=by_issue_jaccard)
    jaccard = issue_jaccard(expected_issue, str(best.get("issue", "")))
    score, reason = await issue_correctness(
        expected_issue, str(best.get("issue", "")), model
    )
    return ExpectationOutcome(
        True, score, reason, best, gate_breakdown(expected, best), jaccard
    )


def expectation_label(expected: dict[str, Any], index: int) -> str:
    """A compact, content-light label for an expectation."""
    number = extract_section_number(str(expected.get("section", ""))) or "?"
    category = expected.get("category", "?")
    severity = expected.get("severity", "?")
    return f"#{index + 1} {category} ≥{severity} §{number}"


def print_match_detail(
    index: int, expected: dict[str, Any], outcome: ExpectationOutcome
) -> None:
    """Print the matched finding and the judge's rationale (shows document content)."""
    finding = outcome.finding or {}
    score = outcome.score or 0.0
    print(
        f"    {expectation_label(expected, index)}  "
        f"{_colour_score(score, f'score {score:.3f}')}  jaccard {outcome.issue_jaccard:.3f}"
    )
    print(f"      matched section: {finding.get('section', '')}")
    print(f"      produced issue:  {finding.get('issue', '')}")
    print(f"      judge:           {outcome.reason}")


def print_miss_detail(
    index: int,
    expected: dict[str, Any],
    finding: dict[str, Any] | None,
    gates: dict[str, bool],
) -> None:
    """Print why an expectation did not match (which gate the near-miss failed)."""
    label = expectation_label(expected, index)
    miss = _red("miss")
    if finding is None:
        print(f"  {miss} {label}: no findings produced")
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
    """Analyse the document once, capture the response, evaluate every expectation.

    The semaphore bounds how many runs are in flight; the duration measures only the
    work, not time spent waiting for a slot.
    """
    async with batch.semaphore:
        start = time.perf_counter()
        data = await analyse_document(batch.client, batch.host, batch.document_id)
        name = f"{batch.stem}-{batch.batch_ts}-run{run_index:0{batch.run_width}d}.json"
        out_path = batch.out_dir / name
        out_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        produced = data.get("findings", [])
        outcomes = [
            await evaluate_expectation(expected, produced, batch.model)
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
    match_text = _colour_ratio(matched, total, f"matches {matched}/{total}")
    score_text = (
        _colour_score(mean, f"mean correctness {mean:.3f}")
        if scores
        else _dim(f"mean correctness {mean:.3f}")
    )
    print(
        f"{_dim(f'[{completed}/{total_runs}]')} {result.out_path.name}   "
        f"{match_text}   {score_text}   {_dim(f'{result.duration:.1f}s')}"
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
        "--runs", type=int, default=5, help="Number of analysis runs (default: 5)."
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
    global _COLOUR  # noqa: PLW0603
    args = parse_args()
    _COLOUR = sys.stdout.isatty() and not args.no_colour
    if args.runs < 1:
        message = "--runs must be at least 1"
        raise SystemExit(message)

    document_path = Path(args.document)
    validate_document(document_path)
    expectations = json.loads(Path(args.expectations).read_text(encoding="utf-8"))[
        "findings"
    ]
    numberless = [
        index + 1
        for index, expected in enumerate(expectations)
        if extract_section_number(str(expected.get("section", ""))) is None
    ]
    if numberless:
        message = (
            f"expectations {numberless} have no section number; section matching "
            "requires one"
        )
        raise SystemExit(message)
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
        match_text = _colour_ratio(matched, args.runs, f"matched {matched}/{args.runs}")
        score_text = (
            _colour_score(mean, f"mean correctness {mean:.3f}")
            if score_counts[index]
            else _dim(f"mean correctness {mean:.3f}")
        )
        print(f"  {expectation_label(expected, index)}: {match_text}   {score_text}")

    judged = sum(score_counts)
    overall_mean = sum(score_sums) / judged if judged else 0.0
    mean_matches = sum(match_counts) / args.runs
    mean_per_run = sum(r.duration for r in results) / len(results) if results else 0.0
    mm_text = _colour_ratio(
        int(mean_matches), total, f"mean matches {mean_matches:.2f}/{total}"
    )
    mc_text = (
        _colour_score(overall_mean, f"mean issue correctness {overall_mean:.3f}")
        if judged
        else _dim(f"mean issue correctness {overall_mean:.3f}")
    )
    print(f"\nOverall: {mm_text}   {mc_text}")
    print(
        _dim(
            f"Timing: elapsed {elapsed:.1f}s   mean per run {mean_per_run:.1f}s   "
            f"({args.runs} runs, concurrency {concurrency})"
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
