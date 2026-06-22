#!/usr/bin/env bash
# Submit a markdown guidance document to the critique endpoint and summarise
# the result. See app/critique/README.md.
#
# Usage:
#   scripts/critique.sh <document.md>                  # critique only (fast)
#   scripts/critique.sh <document.md> <max_iterations> # critique + revise loop
#   scripts/critique.sh <document.md> <max_iterations> <host>
set -euo pipefail

DOC="${1:?usage: scripts/critique.sh <document.md> [max_iterations] [host]}"
MAX_ITERATIONS="${2:-0}"   # 0 = critique only, no revision
HOST="${3:-http://127.0.0.1:8086}"

OUT_DIR="${TMPDIR:-/tmp}/critique"
mkdir -p "$OUT_DIR"
REQUEST="$OUT_DIR/request.json"
RESPONSE="$OUT_DIR/response.json"
REVISED="$OUT_DIR/revised.md"

python3 - "$DOC" "$MAX_ITERATIONS" "$REQUEST" <<'PY'
import json
import sys

doc_path, max_iterations, request_path = sys.argv[1:4]
with open(doc_path) as f:
    doc = f.read()

payload = {"document_text": doc}
if int(max_iterations) > 0:
    payload["revise"] = True
    payload["max_iterations"] = int(max_iterations)

with open(request_path, "w") as f:
    json.dump(payload, f)
mode = f"revise, max_iterations={max_iterations}" if int(max_iterations) > 0 else "critique only"
print(f"payload: {len(doc)} chars ({mode})")
PY

echo "POSTing to $HOST/critique/analyse — critique-only takes a few minutes; revision loops on large documents can take 10-20..."
curl -sS -X POST "$HOST/critique/analyse" \
  -H "Content-Type: application/json" \
  -d @"$REQUEST" \
  --max-time 1800 \
  -o "$RESPONSE" \
  -w "HTTP %{http_code} in %{time_total}s\n"

python3 - "$RESPONSE" "$REVISED" <<'PY'
import json
import sys

response_path, revised_path = sys.argv[1:3]
with open(response_path) as f:
    r = json.load(f)

print("status:", r["status"], "| iterations:", r["iterations"])
print(
    "history:",
    [(h["iteration"], h["approved"], h["finding_count"]) for h in r["critique_history"]],
)
print("invariant warnings:", len(r["invariant_warnings"]))
for w in r["invariant_warnings"]:
    print("  !", w)
print("usage:", r["usage"])
for rep in r["reports"]:
    print(f"--- {rep['standard']}: {len(rep['findings'])} findings")
    print(f"    conformance: {rep['conformance_summary'][:200]}")
    for finding in rep["findings"]:
        print(f"  [{finding['severity']}] {finding['rule_reference']} @ {finding['where']}")
        print(f"      what:  {finding['what']}")
        print(f"      quote: {finding['quote'][:160]!r}")
        print(f"      fix:   {finding['fix'][:160]}")

if r.get("revised_document"):
    with open(revised_path, "w") as f:
        f.write(r["revised_document"])
    print("\nrevised document written to:", revised_path)
else:
    print("\nno revision produced (critique-only run or approved unchanged)")
PY
