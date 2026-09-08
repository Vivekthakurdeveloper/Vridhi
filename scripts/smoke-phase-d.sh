#!/usr/bin/env bash
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

email="phased-$(date +%s)@example.com"
password="Password123!"

echo "== register =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase D\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase D Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== features =="
feats=$(curl -sf "$API/v1/features")
echo "$feats" | grep -q '"google_drive_enabled":true' || {
  echo "FAIL: google_drive_enabled is not true. Set GOOGLE_DRIVE_ENABLED=true and GOOGLE_DRIVE_MODE=mock."
  echo "$feats"
  exit 1
}

echo "== oauth start (mock) =="
hdrs="$(mktemp)"
curl -s -D "$hdrs" -o /dev/null -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
  "$API/v1/connections/google_drive/oauth/start"
loc=$(tr -d '\r' < "$hdrs" | awk 'tolower($1)=="location:"{print $2; exit}')
rm -f "$hdrs"
[[ -n "$loc" ]] || { echo "FAIL: missing OAuth Location header"; exit 1; }
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$loc")
[[ "$code" == "302" || "$code" == "303" || "$code" == "200" ]] || {
  echo "FAIL: oauth callback returned HTTP $code for $loc"
  exit 1
}

echo "== connection detail =="
detail=$(curl -sf -b "$COOKIE_JAR" "$API/v1/connections/google_drive")
echo "$detail" | grep -q '"connected":true' || {
  echo "FAIL: Drive not connected after mock OAuth"
  echo "$detail"
  exit 1
}

echo "== folders =="
folders=$(curl -sf -b "$COOKIE_JAR" "$API/v1/connections/google_drive/folders")
echo "$folders" | grep -q 'folder-contracts' || {
  echo "FAIL: expected mock folders"
  echo "$folders"
  exit 1
}

echo "== select folders =="
curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -X PUT -d '{"folder_ids":["folder-contracts","folder-hr","folder-finance"]}' \
  "$API/v1/connections/google_drive/folders" >/dev/null

echo "== sync now =="
sync=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"visibility":"org","incremental":false}' \
  "$API/v1/connections/google_drive/sync")
job_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" <<<"$sync")
echo "drive_sync_job=$job_id"

echo "== wait for drive sync =="
ready=0
for i in $(seq 1 60); do
  job=$(curl -sf -b "$COOKIE_JAR" "$API/v1/jobs/$job_id")
  status=$(python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$job")
  done_n=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('progress_done',0))" <<<"$job")
  total=$(python3 -c "import json,sys; print(json.load(sys.stdin).get('progress_total',0))" <<<"$job")
  echo "  attempt $i status=$status progress=$done_n/$total"
  if [[ "$status" == "succeeded" ]]; then
    ready=1
    break
  fi
  if [[ "$status" == "dead" || "$status" == "failed" ]]; then
    echo "$job"
    echo "FAIL: drive sync ended in $status"
    exit 1
  fi
  sleep 2
done
[[ "$ready" == "1" ]] || { echo "FAIL: timed out waiting for drive sync"; exit 1; }

echo "== wait for child ingest jobs =="
docs_ready=0
for i in $(seq 1 60); do
  docs=$(curl -sf -b "$COOKIE_JAR" "$API/v1/documents?limit=50")
  count=$(python3 -c "
import json,sys
data=json.load(sys.stdin)
ready=[d for d in data.get('items',[]) if d.get('source')=='google_drive' and d.get('status')=='ready']
print(len(ready))
" <<<"$docs")
  echo "  attempt $i drive_ready_docs=$count"
  if [[ "$count" -ge 1 ]]; then
    docs_ready=1
    break
  fi
  sleep 2
done
[[ "$docs_ready" == "1" ]] || { echo "FAIL: no ready Drive documents"; exit 1; }

echo "== sync history / failed docs =="
curl -sf -b "$COOKIE_JAR" "$API/v1/connections/google_drive/syncs" | grep -q "$job_id"
curl -sf -b "$COOKIE_JAR" "$API/v1/connections/google_drive/failed-documents" >/dev/null

echo "== connectors catalog shows connected =="
# After disconnect we reconnect? No — still connected until disconnect step.
curl -sf -b "$COOKIE_JAR" "$API/v1/connectors" | grep -q '"id":"google_drive"' 

echo "== disconnect =="
curl -sf -b "$COOKIE_JAR" -X DELETE "$API/v1/connections/google_drive" >/dev/null
after=$(curl -sf -b "$COOKIE_JAR" "$API/v1/connections/google_drive")
echo "$after" | grep -q '"connected":false' || {
  echo "FAIL: expected disconnected"
  echo "$after"
  exit 1
}

echo "PHASE D SMOKE PASSED"
