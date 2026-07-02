# Checker evaluation scripts

Harnesses that exercise the live checkers and evaluate their findings. All
drive the running stack purely through its public HTTP APIs — the same
contract the frontend uses, with no knowledge of Mongo, S3 or any app
internals.

- `publishing_evaluate.py` — score the publishing checker's findings against
  a ground-truth expectations file.
- `publishing_stability.py` — measure the publishing checker's run-to-run
  reproducibility across N runs (no ground truth).
- `critique_evaluate.py` — score the critique checker's findings against a
  ground-truth expectations file.

Each evaluation script has its own expectations file format, so name the
files per checker: `publishing-expectations.json`,
`critique-expectations.json`.

## Prerequisites: the environment under test

The harnesses can currently only run against a **locally deployed Docker
stack**, controlled by the local-dev orchestrator repository:
<https://github.com/DEFRA/ai-uc-rpa-guidance-dev>.

In the orchestrator repo:

1. Clone the service repos: `uv run task clone`.
2. **Copy `.env.example` to `.env` and set all the necessary parameters
   correctly.** This is essential — the checker runs on AWS Bedrock, so the
   Bedrock credentials and `CLAUDE_SONNET_MODEL_CONFIG` must be real values,
   not the dummy placeholders.
3. Start the services under test plus their dependencies:
   `docker compose up --build`.

That brings up the endpoints the scripts default to:

- guidance backend — `http://localhost:8085` (`--host`)
- cdp-uploader — `http://localhost:7337` (`--uploader`)

In **this** repo:

- `uv sync` to install dependencies.
- Copy `.env.example` to `.env` and set real Bedrock credentials here too.
  The LLM judge that scores findings runs *inside the script process* (via
  `app.infra.bedrock`), not inside Docker, so the scripts need their own
  working Bedrock config. Both scripts auto-load the nearest `.env`
  (`find_dotenv`), so no `--env-file` flag is needed.
  Never commit or echo the credential values.

## `publishing_evaluate.py` — evaluate against ground truth

### How it works

The script uploads the `.docx` to the cdp-uploader once (reusing an
already-parsed document whose `contentHash` matches, exactly as the frontend
would) and waits for parsing. Then, for each run, it submits the document for
analysis (`POST /publishing/analyse`) and polls the job
(`GET /publishing/jobs/{jobId}`) until it completes. Each expected finding is
matched against the run's produced findings and scored:

1. **Section gate** — every finding is expected to belong to one specific
   section, and that section only. The expectation's section number is
   extracted (e.g. `5.2` from `Section 5.2 Check CRM`) and must appear exactly
   in the produced finding's section; titles are ignored. Expectations without
   a section number are rejected up front.
2. **Category gate** — exact match.
3. **Severity gate** — produced severity must be at least the expected level
   (`info < low < medium < high < critical`).
4. **Judge** — among the gate-passers, the finding whose issue wording is most
   similar (jaccard) is the match, and an LLM judge scores 0.0–1.0 how fully
   its issue text identifies the same underlying problem as the expectation.

Misses are reported with a per-gate breakdown of the nearest near-miss, so a
failed expectation explains how close the checker came.

### Building an expectations file

Ground truth has to be farmed by hand. Two routes:

**From a verified candidate run.** Run the frontend and backend using docker from
the parent development project.

1. Upload a RPA guidance document for publishing review at [http://localhost:3000/guidance-documents](http://localhost:3000/guidance-documents).
2. Run the publishing check from [http://localhost:3000/publishing-checks/start](http://localhost:3000/publishing-checks/start).
3. Find the document's id: [http://localhost:8085/guidance/documents/](http://localhost:8085/guidance/documents/)
   returns a JSON listing whose items each carry the document `id`.
4. Once the publish run has completed, fetch its findings in JSON form from
   [http://localhost:8085/publishing/documents/{documentId}/analysis](http://localhost:8085/publishing/documents/{documentId}/analysis).
5. Manually verify which findings are acceptable ground truth, and copy those
   payloads into the expectations file, say `publishing-expectations.json`.

**Hand-authored.** Write the findings directly, in the same shape.

The file is a subset of the analyse response — only `category`, `section`,
`severity` and `issue` are used, and every entry must contain a section
number. Anonymised example (same structure as a real expectations file):

```json
{
  "findings": [
    {
      "category": "overall_publish_readiness",
      "section": "Section 5.2 Check the case record",
      "issue": "The following sentence is duplicated word-for-word within the same bullet point: \"Make a note of the reason for the change and refer to the\" appears twice before the link to the follow-up section.",
      "severity": "medium"
    },
    {
      "category": "overall_publish_readiness",
      "section": "Section 7.2 Email — case note template",
      "issue": "The case note template contains a typo: 'Reference ID not linked to XZY in the tracking system' — 'XZY' should be 'XYZ'.",
      "severity": "medium"
    }
  ]
}
```

Extra fields (`why_it_matters`, `recommendation`, `confidence`, …) are
tolerated and ignored, so verified findings can be pasted in whole.

### Running it

```bash
uv run scripts/publishing_evaluate.py \
    scripts/input.docx scripts/publishing-expectations.json
```

- `--runs N` — number of analysis runs (default 5); runs execute concurrently
  (bounded by `--concurrency`, default 5) and results are aggregated across
  them: per-expectation match rate, and issue correctness averaged across all
  matched issues.
- `--host` / `--uploader` — override the stack endpoints, use the defaults if
  running locally.
- `--out-dir` — where captured run files go (default: the document's
  directory).
- `--show-reasons` — print the judge's rationale for each match.

Each run's raw analysis response is captured as
`<doc-stem>-publishing-<batch-utc>-runNN.json`; the checker infix keeps
captures of different checkers apart for the same document, and every file of
one invocation shares the batch timestamp, so batches never overwrite each
other and sort chronologically. These captures are valid inputs to `publishing_stability.py`
(one batch per invocation) should you wish to evaluate the batch for stability.
Note that `publishing_stability.py` can execute a batch of runs against a
specified document itself, independent of `publishing_evaluate.py`.

Example:

```bash
$ uv run ./publishing_evaluate.py input.docx publishing-expectations.json --runs 2
Document: input.docx   Expectations: 2   Runs: 2   Concurrency: 2   Host: http://localhost:8085
Document id: efaa601d-bda4-4e08-9ea0-841f3875e60d
[1/2] input-publishing-20260702T162410Z-run02.json   matches 2/2   mean correctness 0.950   140.3s
[2/2] input-publishing-20260702T162410Z-run01.json   matches 2/2   mean correctness 0.975   155.8s

Per-expectation match rate across runs:
  #1 overall_publish_readiness ≥medium §5.2: matched 2/2   mean correctness 1.000
  #2 overall_publish_readiness ≥medium §7.2: matched 2/2   mean correctness 0.925

Overall: mean matches 2.00/2   mean issue correctness 0.963
Timing: elapsed 155.9s   mean per run 148.1s   (2 runs, concurrency 2)
```

## `publishing_stability.py` — run-to-run reproducibility

The checker is non-deterministic: the same document analysed twice yields
overlapping-but-different findings. This harness quantifies that overlap
across N runs using only the runs themselves — no ground truth.

Findings are blocked by section number, then clustered within each block:
lexical jaccard settles the clearly-same and clearly-different pairs, and an
LLM judge decides only the ambiguous middle band. Each resulting cluster is
one distinct issue; its support (the number of runs it appeared in) drives the
report: a per-issue consistency table, a support histogram, and a pairwise
soft-Dice agreement score between runs.

Compare previously captured run files. The files of one evaluation batch all
share the `<doc-stem>-publishing-<batch-utc>` prefix, so globbing on that
prefix selects exactly that batch's runs:

```bash
uv run scripts/publishing_stability.py \
    scripts/<doc-stem>-publishing-<batch-utc>-run*.json [--match-report]
```

Pass one batch at a time: run numbers restart at `run01` in every batch, and
the script identifies runs by that number, so use a common prefix from a run
batch to ensure matching files are used in the stability check.

Example: _Note that for a high number of findings within the same section for
a high number of runs the matching process will take excessive time._

```bash
$ uv run ./publishing_stability.py --exclude-categories links input-publishing-20260702T163121Z-run0*
[stability] section 1/11: §4 (3 findings) — 0 judge calls so far
[stability] section 2/11: §4.1 (5 findings) — 3 judge calls so far
[stability] section 3/11: §4.2 (1 findings) — 11 judge calls so far
[stability] section 4/11: §5.2 (10 findings) — 11 judge calls so far
[stability] section 5/11: §6 (8 findings) — 37 judge calls so far
[stability] section 6/11: §7.2 (9 findings) — 63 judge calls so far
[stability] section 7/11: §7.3 (5 findings) — 83 judge calls so far
[stability] section 8/11: §8 (5 findings) — 89 judge calls so far
[stability] section 9/11: §2 (9 findings) — 95 judge calls so far
[stability] section 10/11: §5.1 (1 findings) — 121 judge calls so far
[stability] section 11/11: §5.1.1 (1 findings) — 121 judge calls so far
Runs: 5
  run01: 9 findings
  run02: 12 findings
  run03: 9 findings
  run04: 12 findings
  run05: 15 findings
Excluded categories: links

Pairwise agreement (soft Dice over 10 run pairs): mean 0.678   sd 0.133   min 0.526
Distinct issues: 19   in all 5 runs: 5
  in 5/5 runs: 5
  in 4/5 runs: 1
  in 3/5 runs: 5
  in 2/5 runs: 1
  in 1/5 runs: 7
Judge tokens over 121 calls: 113214 in, 16144 out (129358 total)

Per-issue consistency (most stable first):
  5/5  §5.2  [overall_publish_readiness]  [high]  The same duplicated phrase …
  5/5  §6  [overall_publish_readiness]  [high, moderate]  The 'No' bullet under …
  5/5  §7.2  [overall_publish_readiness]  [high]  The case note template contains …
  5/5  §7.3  [images_and_formatting]  [high, moderate]  The image in Section 7.3 has …
  5/5  §8  [overall_publish_readiness]  [moderate]  The text says …
  4/5  §4.1  [overall_publish_readiness]  [high]  The link text and surrounding text …
  3/5  §2  [overall_publish_readiness]  [high]  The bullet point …
  3/5  §2  [overall_publish_readiness]  [high]  The link text for …
  3/5  §4  [overall_publish_readiness]  [high]  The Note paragraph refers to …
  3/5  §5.2  [overall_publish_readiness]  [high]  The text refers …
```

Or generate fresh runs first (needs the stack running):

```bash
uv run scripts/publishing_stability.py \
    --document scripts/input.docx
```

- `--runs N` — number of runs to generate from `--document` (default 5).
- `--exclude-categories links` — drop a category (e.g. link checks) before
  comparing.
- `--low` / `--high` / `--threshold` — the jaccard band that goes to the
  judge, and the same-problem cut-off for clustering.
- `--concurrency` (judge LLM calls) and `--run-concurrency` (analyses in
  flight when generating) are separate controls.
- `--match-report` — also write an Excel workbook laying each issue out with
  every run's wording of it side by side in the order in which the findings
  occur in the document under review. This provides a clear visual
  representation to what degree findings are common across successive runs.

## `critique_evaluate.py` — evaluate the critique checker against ground truth

The critique analogue of `publishing_evaluate.py`, with the same run loop:
upload/reuse the `.docx` once, then for each run submit a critique job
(`POST /critique/jobs`) and poll it (`GET /critique/jobs/{jobId}`) to
completion. Critique-only runs still take a few minutes each.

The critique response differs from publishing in two ways the evaluation has
to respect:

- **Two report sections.** The result carries one report per standard —
  `defra_style` and `gds` — and the standards are a hard partition: an
  expectation is only ever compared with findings from its own standard's
  report. Matching, ranking, judging and near-miss diagnostics never cross
  reports.
- **`where`, not `section`.** A critique finding locates itself with a free-text
  `where` that may name several sections (e.g. "Sections 3.2 and 5.1"). Each
  produced finding is expanded into one candidate per section number in its
  `where` before matching, so a multi-location finding can satisfy an
  expectation at any one of its locations.

Each expected finding is matched against its standard's candidates and scored:

1. **Section gate** — the expectation's `where` must contain exactly one
   section number (rejected up front otherwise; split multi-section ground
   truth into one entry per section), and it must equal the candidate's
   extracted section number.
2. **Severity gate** — produced severity must be at least the expected level
   (`low < medium < high < critical`).
3. **Judge** — among the gate-passers, the finding whose `what` wording is
   most similar (jaccard) is the match, and an LLM judge scores 0.0–1.0 how
   fully its problem description identifies the same underlying problem as
   the expectation.

Misses are reported with a per-gate breakdown of the nearest near-miss within
the same standard.

### Building an expectations file

Ground truth is farmed by hand, exactly as for publishing — from a verified
candidate run (the captured `<doc-stem>-critique-<batch-utc>-runNN.json`
files, or `GET /publishing/documents/{documentId}/analysis`'s critique
counterpart `GET /critique/documents/{documentId}/analysis`) or hand-authored.
A run with an empty expectations file (`{"findings": {}}`) still captures the
run outputs, which is a convenient way to bootstrap.

Findings are keyed by the standard whose report they came from; either key
may be omitted. Only `where`, `severity` and `what` are used; extra fields
(`rule_reference`, `quote`, `why`, `fix`, …) are tolerated and ignored, so
verified findings can be pasted in whole. Anonymised example:

```json
{
  "findings": {
    "defra_style": [
      {
        "where": "Section 5.2 Check the case record",
        "severity": "medium",
        "what": "Uses 'RLE' without expanding the abbreviation on first use."
      }
    ],
    "gds": [
      {
        "where": "Section 7.2 Email templates",
        "severity": "low",
        "what": "Sentence exceeds 25 words, breaching the plain-English guidance."
      }
    ]
  }
}
```

### Running it

```bash
uv run scripts/critique_evaluate.py \
    scripts/input.docx scripts/critique-expectations.json
```

Flags are identical to `publishing_evaluate.py` (`--runs`, `--concurrency`,
`--host`, `--uploader`, `--out-dir`, `--show-reasons`, `--no-colour`). Each
run's raw critique response is captured as
`<doc-stem>-critique-<batch-utc>-runNN.json`, following the same
checker-infixed naming as the publishing captures.

Example:

```bash
$ uv run ./critique_evaluate.py input.docx critique-expectations.json --runs 3
Document: input.docx   Expectations: 2   Runs: 3   Concurrency: 3   Host: http://localhost:8085
Document id: efaa601d-bda4-4e08-9ea0-841f3875e60d
[1/3] input-critique-20260702T162435Z-run01.json   matches 2/2   mean correctness 0.850   99.5s
[2/3] input-critique-20260702T162435Z-run02.json   matches 2/2   mean correctness 0.425   101.7s
[3/3] input-critique-20260702T162435Z-run03.json   matches 2/2   mean correctness 0.875   216.6s

Per-expectation match rate across runs:
  #1 gds ≥medium §5.1: matched 3/3   mean correctness 0.483
  #2 defra_style ≥medium §1: matched 3/3   mean correctness 0.950

Overall: mean matches 2.00/2   mean what correctness 0.717
Timing: elapsed 216.6s   mean per run 139.2s   (3 runs, concurrency 3)
```

## Notes

- The `.gitignore` in this directory deliberately ignores documents, captured
  runs and reports (`*.docx`, `*.json`, `*.xlsx`, …) so evaluation artefacts
  are never committed.
