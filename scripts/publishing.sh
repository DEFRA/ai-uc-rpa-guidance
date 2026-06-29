#!/usr/bin/env bash
# Black-box client for the RPA Guidance publishing QA flow.
#
# Drives ONLY the public HTTP APIs of the running stack — the guidance backend
# and the CDP uploader — with no knowledge of Mongo, S3, or any internals. It
# reproduces what the frontend does: initiate an upload, push the .docx to the
# uploader, wait for parsing, then submit the parsed document for analysis and
# poll the job to completion.
#
# Before uploading it checks whether the document is already there: it hashes the
# file and looks for a COMPLETE document with a matching contentHash, reusing it
# instead of uploading again.
#
# Usage:
#   scripts/publishing.sh [--raw] <document.docx> [--api URL] [--uploader URL]
#
# Defaults target the orchestrator stack (`docker compose up` in the -dev root):
#   --api       http://localhost:8085   guidance backend
#   --uploader  http://localhost:7337   cdp-uploader (direct published port)
# Override via flags or PUBLISHING_API_URL / CDP_UPLOADER_URL.
#
# Progress/diagnostics go to stderr; with --raw, stdout is the final job JSON.
set -euo pipefail

API="${PUBLISHING_API_URL:-http://localhost:8085}"
UPLOADER="${CDP_UPLOADER_URL:-http://localhost:7337}"
RAW=0
DOC=""

PARSE_TIMEOUT=180   # seconds to wait for upload + parse to reach 'complete'
ANALYSE_TIMEOUT=600 # seconds to wait for the analysis job to finish
POLL_INTERVAL=2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --raw | -r) RAW=1; shift ;;
    --api) API="${2:?--api needs a URL}"; shift 2 ;;
    --uploader) UPLOADER="${2:?--uploader needs a URL}"; shift 2 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) DOC="$1"; shift ;;
  esac
done

log() { echo "$@" >&2; }
die() { echo "error: $*" >&2; exit 1; }

[[ -n "$DOC" ]] || die "usage: scripts/publishing.sh [--raw] <document.docx> [--api URL] [--uploader URL]"
[[ -f "$DOC" ]] || die "file not found: $DOC"
[[ "$(head -c 2 "$DOC")" == "PK" ]] || die "not a valid .docx (expected a PK/zip file): $DOC"
command -v python3 >/dev/null || die "python3 is required"
command -v openssl >/dev/null || die "openssl is required"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Base64 SHA-256 of the file — the same form the uploader reports as contentHash.
HASH="$(openssl dgst -sha256 -binary "$DOC" | base64)"
log "document:  $DOC"
log "sha256:    $HASH"
log "api:       $API"
log "uploader:  $UPLOADER"

# --- small JSON helpers (pure response parsing, no app internals) -------------

# find_complete_id_by_hash <list.json> <hash> -> prints documentId or nothing
find_complete_id_by_hash() {
  python3 - "$1" "$2" <<'PY'
import json, sys
data = json.load(open(sys.argv[1])); want = sys.argv[2]
for it in data.get("items", []):
    if it.get("status") == "complete" and it.get("contentHash") == want:
        print(it.get("id")); break
PY
}

# ids_in <list.json> -> prints all document ids, one per line
ids_in() {
  python3 - "$1" <<'PY'
import json, sys
for it in json.load(open(sys.argv[1])).get("items", []):
    print(it.get("id"))
PY
}

# status_of <list.json> <id> -> prints that document's status (or nothing)
status_of() {
  python3 - "$1" "$2" <<'PY'
import json, sys
data = json.load(open(sys.argv[1])); want = sys.argv[2]
for it in data.get("items", []):
    if it.get("id") == want:
        print(it.get("status", "")); break
PY
}

# json_field <file.json> <key> -> prints a top-level string field (or nothing)
json_field() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],""))' "$1" "$2"
}

# fetch the document list into $WORK/list.json (page_size capped at 100 by the API)
fetch_list() {
  curl -fsS "$API/guidance/documents/?page=1&page_size=100" -o "$WORK/list.json"
}

# --- 1. dedup: already uploaded & parsed? ------------------------------------

fetch_list
DOC_ID="$(find_complete_id_by_hash "$WORK/list.json" "$HASH")"

if [[ -n "$DOC_ID" ]]; then
  log "already uploaded: reusing complete document $DOC_ID"
else
  log "not found in existing documents — uploading"

  # snapshot existing ids so we can identify the newly-created document
  ids_in "$WORK/list.json" | sort > "$WORK/ids.before"

  # 2. initiate an upload session via the backend -> uploadId
  TITLE="$(basename "$DOC" .docx)"
  curl -fsS -X POST "$API/guidance/documents/" \
    -H "Content-Type: application/json" \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"title": sys.argv[1], "redirect": "http://localhost/uploaded"}))' "$TITLE")" \
    -o "$WORK/initiate.json"
  UPLOAD_ID="$(json_field "$WORK/initiate.json" uploadId)"
  [[ -n "$UPLOAD_ID" ]] || die "no uploadId in initiate response: $(cat "$WORK/initiate.json")"
  log "upload session: $UPLOAD_ID"

  # 3. push the file bytes straight to the uploader (multipart field 'file')
  DOCX_MIME="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  UP_CODE="$(curl -sS -o "$WORK/upload.out" -w '%{http_code}' \
    -X POST "$UPLOADER/upload-and-scan/$UPLOAD_ID" \
    -F "file=@$DOC;type=$DOCX_MIME")"
  # the uploader answers with a redirect (3xx) to the initiate 'redirect' URL
  case "$UP_CODE" in
    2* | 3*) log "uploaded (HTTP $UP_CODE), scanning + parsing…" ;;
    *) die "upload failed (HTTP $UP_CODE): $(cat "$WORK/upload.out")" ;;
  esac

  # discover the new document id (the one not present before initiate)
  DOC_ID=""
  deadline=$((SECONDS + PARSE_TIMEOUT))
  while [[ $SECONDS -lt $deadline ]]; do
    fetch_list
    ids_in "$WORK/list.json" | sort > "$WORK/ids.after"
    NEW="$(comm -13 "$WORK/ids.before" "$WORK/ids.after" | head -n1)"
    if [[ -n "$NEW" ]]; then DOC_ID="$NEW"; break; fi
    sleep "$POLL_INTERVAL"
  done
  [[ -n "$DOC_ID" ]] || die "timed out waiting for the document to appear"
  log "document id:    $DOC_ID"

  # 4. wait for parsing to reach 'complete'
  deadline=$((SECONDS + PARSE_TIMEOUT))
  while [[ $SECONDS -lt $deadline ]]; do
    fetch_list
    ST="$(status_of "$WORK/list.json" "$DOC_ID")"
    case "$ST" in
      complete) log "parsed (status=complete)"; break ;;
      failed | error) die "parsing failed (status=$ST)" ;;
      *) sleep "$POLL_INTERVAL" ;;
    esac
  done
  [[ "$(status_of "$WORK/list.json" "$DOC_ID")" == "complete" ]] || die "timed out waiting for parse to complete"
fi

# --- 5. submit for analysis --------------------------------------------------

AN_CODE="$(curl -sS -o "$WORK/analyse.json" -w '%{http_code}' \
  -X POST "$API/publishing/analyse" \
  -H "Content-Type: application/json" \
  -d "{\"documentId\": \"$DOC_ID\"}")"
case "$AN_CODE" in
  202) : ;;
  404) die "backend says document not found ($DOC_ID)" ;;
  409) die "backend says document not fully parsed ($DOC_ID)" ;;
  *) die "analyse request failed (HTTP $AN_CODE): $(cat "$WORK/analyse.json")" ;;
esac
JOB_ID="$(json_field "$WORK/analyse.json" jobId)"
[[ -n "$JOB_ID" ]] || die "no jobId in analyse response: $(cat "$WORK/analyse.json")"
log "analysis job:   $JOB_ID — running…"

# --- 6. poll the job to completion -------------------------------------------

deadline=$((SECONDS + ANALYSE_TIMEOUT))
while [[ $SECONDS -lt $deadline ]]; do
  curl -fsS "$API/publishing/jobs/$JOB_ID" -o "$WORK/job.json"
  ST="$(json_field "$WORK/job.json" status)"
  case "$ST" in
    completed) break ;;
    error) die "analysis job errored: $(json_field "$WORK/job.json" errorMessage)" ;;
    *) sleep "$POLL_INTERVAL" ;;
  esac
done
[[ "$(json_field "$WORK/job.json" status)" == "completed" ]] || die "timed out waiting for analysis to complete"

# --- 7. report ---------------------------------------------------------------

if [[ "$RAW" -eq 1 ]]; then
  cat "$WORK/job.json"
  exit 0
fi

python3 - "$WORK/job.json" <<'PY'
import json, sys
job = json.load(open(sys.argv[1]))
r = job.get("result") or {}
print("document:", r.get("document_title", "?"))
print("verdict: ", r.get("verdict", "?"))
print("summary: ", r.get("summary", ""))
findings = r.get("findings", [])
print(f"findings: {len(findings)}")
for f in findings:
    sev = f.get("severity", "?"); conf = f.get("confidence", "?")
    print(f"  [{sev}/{conf}] {f.get('category','?')} – {f.get('section','?')}")
    print(f"      issue: {f.get('issue','')}")
    print(f"      why:   {f.get('why_it_matters','')}")
    print(f"      fix:   {f.get('recommendation','')}")
good = r.get("good_points", [])
print(f"good points: {len(good)}")
for g in good:
    print(f"  + {g}")
PY
