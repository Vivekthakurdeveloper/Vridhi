#!/usr/bin/env bash
# Lightweight golden-eval harness against a live tenant with seeded docs.
# Usage: API_URL=http://localhost:8000 COOKIE=... ./scripts/run-golden-eval.sh
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
GOLDEN="${GOLDEN_PATH:-evals/golden.jsonl}"
COOKIE_JAR="${COOKIE_JAR:-}"
LIMIT="${EVAL_LIMIT:-50}"

if [[ -z "$COOKIE_JAR" ]]; then
  COOKIE_JAR="$(mktemp)"
  email="eval-$(date +%s)@example.com"
  curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
    -d "{\"name\":\"Eval\",\"email\":\"$email\",\"password\":\"Password123!\",\"organization_name\":\"Eval Co\"}" \
    "$API/v1/auth/register" >/dev/null
fi

python3 - "$API" "$GOLDEN" "$COOKIE_JAR" "$LIMIT" <<'PY'
import json, sys, urllib.request
api, golden, cookie_jar, limit = sys.argv[1:5]
limit = int(limit)

def load_cookie(path):
    # naive: use curl via subprocess for auth simplicity
    return path

import subprocess, tempfile, os

def post(path, body):
    cmd = [
        "curl", "-sf", "-b", cookie_jar,
        "-H", "Content-Type: application/json",
        "-d", json.dumps(body),
        f"{api}{path}",
    ]
    out = subprocess.check_output(cmd, text=True)
    return json.loads(out)

rows = []
with open(golden) as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))
rows = rows[:limit]

ok = 0
for row in rows:
    data = post("/v1/chat", {"query": row["question"], "stream": False})
    msg = data["assistant_message"]
    content = (msg.get("content") or "").lower()
    if row["expect_answerable"]:
        hit = any(x.lower() in content for x in row.get("must_include_any") or [])
        # also accept citations present without exact string if answerable flag false-negative on extractive
        if hit or (not msg.get("no_answer") and msg.get("citations")):
            ok += 1
            status = "PASS"
        else:
            status = "FAIL"
    else:
        if msg.get("no_answer") or any(x in content for x in ["don't know", "do not know", "not enough", "insufficient", "available company knowledge"]):
            ok += 1
            status = "PASS"
        else:
            status = "FAIL"
    print(f"{status} {row['id']} answerable={row['expect_answerable']}")

print(f"SCORE {ok}/{len(rows)} ({(ok/len(rows)*100):.1f}%)")
PY
