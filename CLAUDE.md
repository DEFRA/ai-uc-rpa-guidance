# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This is the **Python backend/runtime** for the RPA Guidance PoC (a DEFRA CDP service). It is a FastAPI app whose one feature so far is the `POST /publishing/analyse` endpoint: it runs an LLM "checker" agent over a guidance document and returns structured QA findings. The agent runs on AWS Bedrock (Claude Sonnet) via `pydantic-ai`.

This repo is normally checked out into `repos/` by the sibling local-dev orchestrator. The `compose.yaml` here stands the service up *alone* with its dependencies (floci/AWS emulator, mongodb, redis); the orchestrator's top-level compose wires it together with the frontend.

## Commands

```bash
uv sync                       # install deps into .venv (use --locked to respect uv.lock)
uv run task test              # lint + typecheck + pytest with coverage (the full gate)
uv run task lint              # ruff format --check + ruff check (no fixes)
uv run task format            # ruff format + ruff check --fix (mutates files)
uv run task typecheck         # mypy ./app ./tests
uv run pytest                 # tests only, no coverage
uv run pytest tests/publishing/test_publishing_router.py::test_name   # single test
uv run --env-file .env ai-uc-rpa-guidance   # run the app locally (reads .env)
docker compose up --build     # run app + floci + mongodb + redis; press `w` for hot-reload
```

`uv run task test` is the CI gate — run it before considering work done. `asyncio_mode = "auto"`, so async test functions need no decorator.

## Architecture

Request flow for the one feature:

```
publishing/router.py        # POST /publishing/analyse, thin — delegates to service
  → publishing/service.py   # orchestrates: builds deps, runs the agent, maps output → API schema
    → publishing/agents/checker.py   # pydantic_ai.Agent; @instructions loads prompt + appends doc text
      → infra/bedrock/llm.py         # the BedrockConverseModel (claude_sonnet) passed in at run time
      → infra/prompts/repository.py  # loads the system prompt from a .md file
```

Key conventions and non-obvious wiring:

- **Two model layers, kept apart on purpose.** `publishing/models.py` holds the *agent's* domain models (`AnalysisOutput`, `AnalysisFinding`, `SeverityLevel`) — what the LLM is asked to produce. `publishing/api_schemas.py` holds the *HTTP* request/response shapes. `service.py` translates between them; don't collapse them.
- **`AgentDependencies` (in `models.py`)** is the `deps` object pydantic-ai injects into the agent. It carries the `document_text` and a `prompt_repository` (defaulting to `FileSystemPromptRepository` pointed at `publishing/prompts/`). Swap the repository here to fake prompts in tests.
- **Prompts are markdown files** in `app/publishing/prompts/` loaded by name (e.g. `checker_hello_world.md`). The repository is an `AbstractPromptRepository`; tests use `FakeFileSystem` (`tests/infra/prompts/fakes.py`) rather than touching disk.
- **Config is a module-level singleton** built at import time: `config.get_config()` caches an `AppConfig` (pydantic-settings). `infra/bedrock/llm.py` calls it at import and instantiates `claude_sonnet` *at import time* — so importing the bedrock module requires valid config (notably `CLAUDE_SONNET_MODEL_CONFIG`). `tests/conftest.py` sets these env vars before anything imports.
- **`CLAUDE_SONNET_MODEL_CONFIG`** is a single env var parsed (`_parse_bedrock_model_config`) into `model_id,inference_profile[,guardrail_id:guardrail_version]`. Add new Bedrock models by extending `BedrockConfig`, not by scattering more raw env vars.
- **Tracing** (`common/tracing.py`): `TraceIdMiddleware` reads the `x-cdp-request-id` header into a `contextvars` trace id and stashes request/response context — this is how logs get correlated. It is added in `entrypoints/fastapi.py`.
- **MongoDB** (`common/mongo.py`): async `pymongo` client, lazily created and cached, opened/closed in the FastAPI `lifespan`. In CDP it loads a custom CA from `common/tls.py` keyed by `MONGO_TRUSTSTORE`.
- **Metrics** (`common/metrics.py`): AWS embedded metrics; only emitted when configured via the `AWS_EMF_*` env vars (see `.env.example`).

## Gotchas

- **Port is inconsistent across files.** `AppConfig.port` defaults to `8086`; the `Dockerfile`/`compose.yaml` use `8085` (debug `8086`); the README's curl example hits `8086`. Set `PORT` explicitly and check which context you're in rather than trusting any single default.
- **Python 3.14** (`requires-python >=3.14`, mypy/ruff target `py314`). mypy runs with `disallow_untyped_defs` — every function needs annotations, including tests' fixtures (tests are otherwise exempt from many rules).
- **Ruff is strict**: bandit (`S`), bugbear (`B`), complexity (`C90`), naming, etc. are all on. `assert` is allowed only under `tests/` and `test_*.py`. Run `uv run task format` to auto-fix before committing.
- **`uv.lock` is committed and `[tool.uv] exclude-newer` pins a date window** — keep installs reproducible with `--locked`; don't hand-edit the lock.
- **Local AWS is faked** by `floci` (LocalStack-style, port 4566); creds in `.env.example` are dummy (`test`/`test`). `compose/floci/start.d/10-setup-resources.sh` provisions local AWS resources at container start.

## Outstanding work

See `TODO.md` — this is an early "hello world" service; the checker prompt, base image hardening, and CI/Sonar/dependabot config are still in progress.
