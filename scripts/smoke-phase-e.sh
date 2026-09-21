#!/usr/bin/env bash
# Phase E smoke: Gmail connect (mock OAuth) -> sync -> attachment ingested -> searchable -> disconnect
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

email="phasee-$(date +%s)@example.com"
password="Password123!"

# Poll a job to a terminal state. Sets JOB to the final payload.
# Fails on failed/dead AND on timeout — a poll loop that falls through
# silently turns a stuck job into a passing test.
wait_for_job() {
  local jid="$1" label="$2" i status=""
  for i in $(seq 1 60); do
    JOB=$(curl -sf -b "$COOKIE_JAR" "$API/v1/jobs/$jid")
    status=$(python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$JOB")
    if [[ "$status" == "succeeded" ]]; then
      return 0
    fi
    if [[ "$status" == "dead" || "$status" == "failed" ]]; then
      echo "$JOB"
      echo "FAIL: $label ended in $status"
      exit 1
    fi
    sleep 2
  done
  echo "$JOB"
  echo "FAIL: timed out waiting for $label (last status=$status)"
  exit 1
}

job_field() { python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',0))" <<<"$JOB"; }

start_sync() {  # $1 = incremental (true|false); echoes the new job id
  local body="{\"incremental\":$1}"
  curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
    -d "$body" "$API/v1/connections/gmail/sync" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])"
}

echo "== register =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase E\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase E Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== features =="
feats=$(curl -sf "$API/v1/features")
echo "$feats" | grep -q '"gmail_enabled":true' || {
  echo "FAIL: gmail_enabled is not true. Set GMAIL_ENABLED=true and GMAIL_MODE=mock."
  echo "$feats"
  exit 1
}

echo "== oauth start (mock) =="
hdrs="$(mktemp)"
curl -s -D "$hdrs" -o /dev/null -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
  "$API/v1/connections/gmail/oauth/start"
loc=$(tr -d '\r' < "$hdrs" | awk 'tolower($1)=="location:"{print $2; exit}')
rm -f "$hdrs"
[[ -n "$loc" ]] || { echo "FAIL: missing OAuth Location header"; exit 1; }
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$loc")
[[ "$code" == "302" || "$code" == "303" || "$code" == "200" ]] || {
  echo "FAIL: oauth callback returned HTTP $code for $loc"
  exit 1
}

echo "== connection detail =="
detail=$(curl -sf -b "$COOKIE_JAR" "$API/v1/connections/gmail")
echo "$detail" | grep -q '"connected":true' || {
  echo "FAIL: Gmail not connected after mock OAuth"
  echo "$detail"
  exit 1
}

echo "== sync now =="
job_id=$(start_sync false)
echo "gmail_sync_job=$job_id"
wait_for_job "$job_id" "gmail sync"
done_n=$(job_field progress_done)
skip_n=$(job_field progress_skipped)
total=$(job_field progress_total)
echo "  progress=$done_n/$total skipped=$skip_n"

# Fixture mailbox: 2 allowed attachments (txt, csv) + 1 image/png outside
# GMAIL_ALLOWED_MIME, which must be skipped rather than failed.
[[ "$total" == "3" ]]   || { echo "FAIL: expected progress_total=3, got $total"; exit 1; }
[[ "$done_n" == "2" ]]  || { echo "FAIL: expected progress_done=2, got $done_n"; exit 1; }
[[ "$skip_n" == "1" ]]  || { echo "FAIL: expected progress_skipped=1 (image/png), got $skip_n"; exit 1; }

echo "== wait for child ingest jobs =="
docs_ready=0
for i in $(seq 1 60); do
  docs=$(curl -sf -b "$COOKIE_JAR" "$API/v1/documents?limit=50")
  count=$(python3 -c "
import json,sys
data=json.load(sys.stdin)
ready=[d for d in data.get('items',[]) if d.get('source')=='gmail' and d.get('status')=='ready']
print(len(ready))
" <<<"$docs")
  echo "  attempt $i gmail_ready_docs=$count"
  if [[ "$count" -ge 2 ]]; then
    docs_ready=1
    break
  fi
  sleep 2
done
[[ "$docs_ready" == "1" ]] || { echo "FAIL: Gmail documents did not reach ready"; exit 1; }

echo "== visibility is private =="
curl -sf -b "$COOKIE_JAR" "$API/v1/documents?limit=50" | python3 -c "
import json,sys
docs=[d for d in json.load(sys.stdin)['items'] if d.get('source')=='gmail']
assert docs, 'no gmail documents'
bad=[d for d in docs if d.get('visibility')!='private']
assert not bad, 'expected all gmail docs private, got ' + repr([(d['title'], d['visibility']) for d in bad])
print('    %d gmail docs, all private' % len(docs))
"

# Phase D's smoke stops at status=ready, which only proves Postgres was written.
# This asserts the chunks actually reached OpenSearch and are retrievable.
echo "== searchable =="
search=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"query":"vendor onboarding checklist penny drop","limit":5}' "$API/v1/search")
echo "$search" | grep -qi "penny drop" || {
  echo "FAIL: gmail attachment not retrievable from the search index"
  echo "$search"
  exit 1
}

echo "== incremental re-sync re-ingests nothing =="
# With a saved Gmail History checkpoint an incremental run reads only what
# changed, so an unchanged mailbox yields no attachments at all and nothing is
# counted as skipped. The invariant is that nothing is re-ingested.
job2=$(start_sync true)
wait_for_job "$job2" "incremental re-sync"
done2=$(job_field progress_done)
skip2=$(job_field progress_skipped)
echo "  done=$done2 skipped=$skip2"
[[ "$done2" == "0" ]] || { echo "FAIL: incremental re-sync re-ingested $done2 attachment(s)"; exit 1; }
[[ "$skip2" =~ ^[0-3]$ ]] || { echo "FAIL: unexpected skip count on re-sync: $skip2"; exit 1; }

echo "== incremental=false forces re-sync =="
# Regression guard: Drive's equivalent flag is a no-op because it consults its
# cursor map unconditionally. Gmail's must actually honour incremental=false.
job3=$(start_sync false)
wait_for_job "$job3" "full re-sync"
done3=$(job_field progress_done)
echo "  done=$done3"
[[ "$done3" == "2" ]] || { echo "FAIL: incremental=false did not force re-sync (done=$done3)"; exit 1; }

echo "== sync history / failed docs =="
curl -sf -b "$COOKIE_JAR" "$API/v1/connections/gmail/syncs" | grep -q "$job_id"
curl -sf -b "$COOKIE_JAR" "$API/v1/connections/gmail/failed-documents" >/dev/null

echo "== connectors catalog =="
curl -sf -b "$COOKIE_JAR" "$API/v1/connectors" | grep -q '"id":"gmail"'

echo "== disconnect =="
curl -sf -b "$COOKIE_JAR" -X DELETE "$API/v1/connections/gmail" >/dev/null
after=$(curl -sf -b "$COOKIE_JAR" "$API/v1/connections/gmail")
echo "$after" | grep -q '"connected":false' || {
  echo "FAIL: expected disconnected"
  echo "$after"
  exit 1
}

echo "PHASE E SMOKE PASSED"
