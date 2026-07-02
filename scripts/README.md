# Publishing checker evaluation scripts

Two harnesses that exercise the live publishing checker and evaluate its
findings. Both drive the running stack purely through its public HTTP APIs —
the same contract the frontend uses, with no knowledge of Mongo, S3 or any
app internals.

- `publishing_evaluate.py` — score produced findings against a ground-truth
  expectations file.
- `publishing_stability.py` — measure run-to-run reproducibility across N
  runs (no ground truth).

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
   payloads into the expectations file, say `expectations.json`.

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
    scripts/input.docx scripts/expectations.json
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
`<doc-stem>-<batch-utc>-runNN.json`; every file of one invocation shares the
batch timestamp, so batches never overwrite each other and sort
chronologically. These captures are valid inputs to `publishing_stability.py`
(one batch per invocation) should you wish to evaluate the batch for stability.
Note that `publishing_stability.py` can execute a batch of runs against a
specified document itself, independent of `publishing_evaluate.py`.

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
share the `<doc-stem>-<batch-utc>` prefix, so globbing on that prefix selects
exactly that batch's runs:

```bash
uv run scripts/publishing_stability.py \
    scripts/<doc-stem>-<batch-utc>-run*.json [--match-report]
```

Pass one batch at a time: run numbers restart at `run01` in every batch, and
the script identifies runs by that number, so use a common prefix from a run
batch to ensure matching files are used in the stability check.

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
  every run's wording of it side by side.

## Notes

- The `.gitignore` in this directory deliberately ignores documents, captured
  runs and reports (`*.docx`, `*.json`, `*.xlsx`, …) so evaluation artefacts
  are never committed.
