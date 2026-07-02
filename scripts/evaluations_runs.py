"""Black-box client for the checker services: upload a document and analyse it.

Shared by the evaluation and stability harnesses to drive the live HTTP flow (the
same public contract ``scripts/publishing.sh`` / ``scripts/critique.sh`` use, no
app internals): upload the .docx once, then submit it to a checker's jobs API and
poll the job to completion — as many times as the caller needs. Publishing and
critique share the job contract, differing only in paths and timeout (publishing's
are the defaults). ``generate_runs`` does exactly that N times and writes each
result to a JSON file.
"""

import asyncio
import base64
import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DOCUMENTS_PATH = "/guidance/documents/"
ANALYSE_PATH = "/publishing/analyse"
JOBS_PATH = "/publishing/jobs"
CRITIQUE_JOBS_PATH = "/critique/jobs"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DEFAULT_HOST = "http://localhost:8085"
DEFAULT_UPLOADER = "http://localhost:7337"
DEFAULT_CONCURRENCY = 5
REQUEST_TIMEOUT_S = 600.0
PARSE_TIMEOUT_S = 180.0
ANALYSE_TIMEOUT_S = 600.0
# A critique run iterates the document for minutes; the analyse timeout is far too
# tight for it.
CRITIQUE_TIMEOUT_S = 1800.0
POLL_INTERVAL_S = 2.0


def capture_name(
    stem: str, checker: str, batch_ts: str, run_index: int, run_width: int
) -> str:
    """The capture filename for one run: ``<stem>-<checker>-<batch-utc>-runNN.json``.

    The checker infix keeps one checker's captures distinguishable from another's
    for the same document; the shared batch timestamp keeps one invocation's files
    together, never overwriting a prior batch, sorting chronologically.
    """
    return f"{stem}-{checker}-{batch_ts}-run{run_index:0{run_width}d}.json"


def content_hash(path: Path) -> str:
    """Base64 SHA-256 of a file -- the form the uploader reports as contentHash."""
    return base64.b64encode(hashlib.sha256(path.read_bytes()).digest()).decode()


def validate_document(document_path: Path) -> None:
    """Reject a missing file or one that is not a .docx (PK/zip), before uploading.

    The uploader runs the file through a Word parser, so a non-.docx (e.g. markdown)
    would only fail later as ``status=failed`` after a full upload + parse cycle.
    """
    if not document_path.is_file():
        message = f"document not found: {document_path}"
        raise SystemExit(message)
    if document_path.read_bytes()[:2] != b"PK":
        message = (
            f"{document_path} is not a .docx (expected a PK/zip file); the uploader "
            "parses Word documents, not markdown — pass the .docx, e.g. input.docx"
        )
        raise SystemExit(message)


async def _list_documents(client: httpx.AsyncClient, host: str) -> list[dict[str, Any]]:
    """All guidance documents (page_size is capped at 100 by the API)."""
    response = await client.get(f"{host}{DOCUMENTS_PATH}?page=1&page_size=100")
    response.raise_for_status()
    return response.json().get("items", [])


async def _wait_for_parse(client: httpx.AsyncClient, host: str, doc_id: str) -> None:
    """Poll the document list until ``doc_id`` reaches status ``complete``."""
    deadline = time.monotonic() + PARSE_TIMEOUT_S
    while time.monotonic() < deadline:
        for item in await _list_documents(client, host):
            if item.get("id") != doc_id:
                continue
            status = item.get("status")
            if status == "complete":
                return
            if status in {"failed", "error"}:
                message = f"document {doc_id} failed to parse (status={status})"
                raise RuntimeError(message)
        await asyncio.sleep(POLL_INTERVAL_S)
    message = f"timed out waiting for document {doc_id} to finish parsing"
    raise RuntimeError(message)


async def resolve_document_id(
    client: httpx.AsyncClient, host: str, uploader: str, docx_path: Path
) -> str:
    """Return the id of a parsed copy of the document, uploading it if absent.

    Black-box: hashes the file and reuses a ``complete`` document with a matching
    contentHash; otherwise initiates an upload, pushes the bytes to the uploader,
    discovers the new id and waits for parsing -- exactly as the frontend does.
    """
    want = content_hash(docx_path)
    existing = await _list_documents(client, host)
    for item in existing:
        if item.get("status") == "complete" and item.get("contentHash") == want:
            return str(item["id"])

    before = {item.get("id") for item in existing}
    initiate = await client.post(
        f"{host}{DOCUMENTS_PATH}",
        json={"title": docx_path.stem, "redirect": "http://localhost/uploaded"},
    )
    initiate.raise_for_status()
    upload_id = initiate.json()["uploadId"]

    with docx_path.open("rb") as handle:
        upload = await client.post(
            f"{uploader}/upload-and-scan/{upload_id}",
            files={"file": (docx_path.name, handle, DOCX_MIME)},
        )
    if upload.status_code >= 400:
        message = f"upload failed (HTTP {upload.status_code}): {upload.text}"
        raise RuntimeError(message)

    deadline = time.monotonic() + PARSE_TIMEOUT_S
    doc_id: str | None = None
    while time.monotonic() < deadline:
        new = {item.get("id") for item in await _list_documents(client, host)} - before
        if new:
            doc_id = str(next(iter(new)))
            break
        await asyncio.sleep(POLL_INTERVAL_S)
    if doc_id is None:
        message = "timed out waiting for the uploaded document to appear"
        raise RuntimeError(message)

    await _wait_for_parse(client, host, doc_id)
    return doc_id


async def analyse_document(
    client: httpx.AsyncClient,
    host: str,
    document_id: str,
    *,
    submit_path: str = ANALYSE_PATH,
    jobs_path: str = JOBS_PATH,
    timeout_s: float = ANALYSE_TIMEOUT_S,
) -> dict[str, Any]:
    """Run one analysis to completion and return its result.

    Submits the document, then polls the job until it completes; raises if the
    job errors or does not finish within the analysis timeout. The publishing
    and critique checkers share this job contract and differ only in the paths
    (and how long a run may take), so both are driven through here.
    """
    submit = await client.post(f"{host}{submit_path}", json={"documentId": document_id})
    submit.raise_for_status()
    job_id = submit.json()["jobId"]

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        poll = await client.get(f"{host}{jobs_path}/{job_id}")
        poll.raise_for_status()
        job = poll.json()
        status = job.get("status")
        if status == "completed":
            return job.get("result") or {}
        if status == "error":
            message = f"analysis job {job_id} errored: {job.get('errorMessage')}"
            raise RuntimeError(message)
        await asyncio.sleep(POLL_INTERVAL_S)
    message = f"timed out waiting for analysis job {job_id} to complete"
    raise RuntimeError(message)


async def generate_runs(
    document_path: Path,
    *,
    runs: int,
    concurrency: int,
    host: str,
    uploader: str,
    out_dir: Path,
    checker: str = "publishing",
    submit_path: str = ANALYSE_PATH,
    jobs_path: str = JOBS_PATH,
    timeout_s: float = ANALYSE_TIMEOUT_S,
    on_run: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """Analyse ``document_path`` ``runs`` times, writing each result to a JSON file.

    The document is uploaded/resolved once; the analyses then run with at most
    ``concurrency`` in flight. Files share one batch timestamp prefix and sort
    chronologically, matching the evaluate scripts' capture naming; ``checker``
    is the filename infix. ``submit_path``/``jobs_path``/``timeout_s`` select the
    checker driven (defaults: publishing). ``on_run`` (if given) is called as each
    run completes, with (completed, total).
    """
    validate_document(document_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    batch_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_width = max(2, len(str(runs)))
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        document_id = await resolve_document_id(client, host, uploader, document_path)

        async def capture(index: int) -> Path:
            nonlocal completed
            async with semaphore:
                data = await analyse_document(
                    client,
                    host,
                    document_id,
                    submit_path=submit_path,
                    jobs_path=jobs_path,
                    timeout_s=timeout_s,
                )
            name = capture_name(document_path.stem, checker, batch_ts, index, run_width)
            path = out_dir / name
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            completed += 1
            if on_run is not None:
                on_run(completed, runs)
            return path

        paths = await asyncio.gather(*(capture(i) for i in range(1, runs + 1)))
    return list(paths)
