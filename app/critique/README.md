# Critique module (CAIT-148 POC)

Automated language/style review of RPA guidance documents against GDS content
guidelines and the [DEFRA style guide](https://digital.defra.gov.uk/content/defra-style-guide),
plus a revised draft with text-level improvements applied.

A **critic** agent reviews the document and produces findings tagged per
standard (`gds` / `defra_style`). Optionally (`"revise": true`), a **writer**
agent applies the findings and the critic re-reviews the revision, looping
until approved or the iteration cap is hit. Revision is **off by default** —
regenerating the full document is by far the slowest stage. Process steps,
information structure, images, and links must survive a revision untouched
(checked programmatically — violations surface as `invariant_warnings`).

Full design and decision log: `specs/CAIT-148-critique-poc-spec.md`.

## Diagrams

- [`docs/diagrams/critique-overview.svg`](../../docs/diagrams/critique-overview.svg) —
  plain-English overview for non-technical readers
- [`docs/diagrams/critique-architecture.svg`](../../docs/diagrams/critique-architecture.svg) —
  technical architecture and request flow

Sources are the `.d2` files alongside them; re-render with
`d2 <name>.d2 <name>.svg` ([D2](https://d2lang.com), `brew install d2`).

## One-time setup

```bash
uv sync

# Only needed to refresh the rules: the context store (GOV.UK style guides +
# writing guidance, DEFRA style guide) is committed under data/context/ and
# baked into the Docker image. Re-run the scraper and commit the diff to update.
uv run --group scraper python scripts/scrape_context.py
```

`.env` needs working Bedrock access. Known-good values for local dev:

```bash
AWS_REGION=eu-west-2
AWS_ACCESS_KEY_ID=...           # real credentials
AWS_SECRET_ACCESS_KEY=...
# bare on-demand model id twice (model_id,inference_profile) — the eu./global.
# cross-region profiles are blocked by a Defra SCP
CLAUDE_SONNET_MODEL_CONFIG=anthropic.claude-sonnet-4-6,anthropic.claude-sonnet-4-6
# show [Critique] progress logs in the terminal
LOG_CONFIG=logging-dev.json
```

## Run the service

```bash
docker compose up -d mongodb              # startup pings Mongo (template behaviour)
uv run --env-file .env ai-uc-rpa-guidance # listens on 127.0.0.1:8086
```

## Submit a document

Easiest — the helper script (payload build + POST + summary + revised doc):

```bash
# critique only (default — fast)
scripts/critique.sh "path/to/guidance-document.md"

# critique + revision loop (slow: writer regenerates the whole document)
scripts/critique.sh "path/to/guidance-document.md" 2
```

Arguments: `<document.md> [max_iterations=0] [host=http://127.0.0.1:8086]` —
`0` means critique-only; any higher value enables the revision loop with that
cap. Outputs land in `$TMPDIR/critique/` (`response.json`, `revised.md`);
compare with `diff "<original>" "$TMPDIR/critique/revised.md"`.

Or by hand:

```bash
# critique only
curl -X POST http://127.0.0.1:8086/critique/analyse \
  -H "Content-Type: application/json" \
  -d '{"document_text": "# My guidance..."}'

# critique + revise
curl -X POST http://127.0.0.1:8086/critique/analyse \
  -H "Content-Type: application/json" \
  -d '{"document_text": "# My guidance...", "revise": true, "max_iterations": 2}'
```

Swagger docs: http://127.0.0.1:8086/docs

## What to expect

- **Timing**: a critique-only run takes a few minutes (the critic reads the
  document plus the style rules it selects). With `revise: true` the writer
  regenerates the whole document as output tokens — on large documents (our
  ~66k-char test document) a 2-iteration loop takes **10–20 minutes**.
  Watch the `[Critique]` log lines for progress.
- **Cost**: critique-only on a small document ≈ 40k input tokens; a full
  2-iteration loop ≈ 80k; a large document considerably more.
- **Response**: `reports` (one per standard: conformance summary + findings
  with rule references), `revised_document` (null unless a revision was
  produced), `critique_history`, `invariant_warnings`, accumulated `usage`.
  `status` is `approved`, `review_completed` (critique-only with findings), or
  `max_iterations_reached`.

## Tuning

| Env var | Default | Meaning |
| --- | --- | --- |
| `CRITIQUE_MAX_ITERATIONS` | `3` | server-side cap on critic/writer loop iterations |
| `CRITIQUE_REQUEST_LIMIT` | `50` | max LLM round-trips per agent run (tool calls included) |
| `CONTEXT_DIRECTORY` | `data/context` | root of the scraped reference document store |
