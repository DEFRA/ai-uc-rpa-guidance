#!/usr/bin/env bash
# Submit a markdown guidance document to the publishing QA endpoint and
# summarise the findings. See README.md (POST /publishing/analyse).
#
# Usage:
#   scripts/publishing.sh <document.md>          # post to the local app
#   scripts/publishing.sh <document.md> <host>   # override host (e.g. compose on :8085)
set -euo pipefail

DOC="${1:?usage: scripts/publishing.sh <document.md> [host]}"
HOST="${2:-http://127.0.0.1:8086}"

OUT_DIR="${TMPDIR:-/tmp}/publishing"
mkdir -p "$OUT_DIR"
REQUEST="$OUT_DIR/request.json"
RESPONSE="$OUT_DIR/response.json"

python3 - "$DOC" "$REQUEST" <<'PY'
import json
import sys

doc_path, request_path = sys.argv[1:3]
with open(doc_path) as f:
    doc = f.read()

with open(request_path, "w") as f:
    json.dump({"document_text": doc}, f)
print(f"payload: {len(doc)} chars")
PY

echo "POSTing to $HOST/publishing/analyse — the LLM analysis can take a minute or two..."
curl -sS -X POST "$HOST/publishing/analyse" \
  -H "Content-Type: application/json" \
  -d @"$REQUEST" \
  --max-time 600 \
  -o "$RESPONSE" \
  -w "HTTP %{http_code} in %{time_total}s\n"

python3 - "$RESPONSE" <<'PY'
import json
import sys

with open(sys.argv[1]) as f:
    r = json.load(f)

print("status:", r["status"])
print("summary:", r["summary"])
findings = r.get("findings", [])
print(f"findings: {len(findings)}")
for finding in findings:
    print(f"  [{finding['severity']}] {finding['section']}")
    print(f"      issue: {finding['issue']}")
    print(f"      fix:   {finding['recommendation']}")
usage = r.get("usage")
if usage:
    print("usage:", usage)
PY
