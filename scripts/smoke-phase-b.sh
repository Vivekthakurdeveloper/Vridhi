#!/usr/bin/env bash
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

email="phaseb-$(date +%s)@example.com"
password="Password123!"

echo "== register =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase B\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase B Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== features =="
feats=$(curl -sf "$API/v1/features")
echo "$feats" | grep -q '"file_upload_enabled":true' || {
  echo "FAIL: file_upload_enabled is not true. Is LocalStack/SQS/S3 configured?"
  echo "$feats"
  exit 1
}

tmpfile="$(mktemp).txt"
echo "Vridhi Phase B smoke test. Revenue grew 12 percent in Q2 for Aranya Foods." > "$tmpfile"
trap 'rm -f "$COOKIE_JAR" "$tmpfile"' EXIT

echo "== upload =="
upload=$(curl -sf -b "$COOKIE_JAR" \
  -F "file=@$tmpfile;type=text/plain" \
  -F "visibility=org" \
  "$API/v1/documents/upload")
doc_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['document']['id'])" <<<"$upload")
job_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['job']['id'])" <<<"$upload")
echo "document=$doc_id job=$job_id"

echo "== wait for ready =="
ready=0
for i in $(seq 1 40); do
  job=$(curl -sf -b "$COOKIE_JAR" "$API/v1/jobs/$job_id")
  status=$(python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$job")
  echo "  attempt $i status=$status"
  if [[ "$status" == "succeeded" ]]; then
    ready=1
    break
  fi
  if [[ "$status" == "dead" || "$status" == "failed" ]]; then
    echo "$job"
    echo "FAIL: job ended in $status"
    exit 1
  fi
  sleep 2
done
[[ "$ready" == "1" ]] || { echo "FAIL: timed out waiting for job"; exit 1; }

echo "== list / get / preview =="
curl -sf -b "$COOKIE_JAR" "$API/v1/documents" | grep -q "$doc_id"
curl -sf -b "$COOKIE_JAR" "$API/v1/documents/$doc_id" | grep -q '"status":"ready"'
preview=$(curl -sf -b "$COOKIE_JAR" "$API/v1/documents/$doc_id/preview")
echo "$preview" | grep -qi "Revenue" || { echo "FAIL: preview missing content"; echo "$preview"; exit 1; }

echo "== delete =="
curl -sf -b "$COOKIE_JAR" -X DELETE "$API/v1/documents/$doc_id" >/dev/null

echo "PHASE B SMOKE PASSED"
