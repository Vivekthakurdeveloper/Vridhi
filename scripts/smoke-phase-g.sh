#!/usr/bin/env bash
# Phase G smoke: submit fixture DWD key -> sync groups (mock) -> Drive file
# shared with a group -> group member finds it -> permission revoked ->
# re-sync -> file becomes inaccessible even though content didn't change.
# This last step is the core scenario this whole phase exists to fix: it is
# not enough for the two sync jobs to merely "succeed" -- the member's actual
# access must flip from visible to invisible in between, in BOTH places that
# answer "can this user see this document": the Postgres-backed documents API
# and the OpenSearch-backed /v1/search path (which is what retrieval and RAG
# actually authorise against).
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Test-only side channel: apps/api/app/services/drive.py's get_mock_files()
# reads this file (if present) to override a mock file's `permissions` list
# at call time. It's how this host-side script mutates what the api/worker
# containers see between the two syncs below, since docker-compose
# bind-mounts ./apps/api into both. See drive.py for the mock-mode-only
# guarantee. Removed on exit so re-runs (and non-smoke mock usage) start clean.
OVERRIDES_FILE="$REPO_ROOT/apps/api/.mock-drive-overrides.json"
FINANCE_GROUP_FILE_ID="file-finance-group-shared"

# Path to hand to python3's open(): on Windows/git-bash, python3 is a native
# Windows build that doesn't understand /c/... paths, so translate via
# cygpath when available; elsewhere the POSIX path is already right.
py_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}
OVERRIDES_FILE_PY="$(py_path "$OVERRIDES_FILE")"

COOKIE_JAR="$(mktemp)"
member_jar="$(mktemp)"
cleanup() { rm -f "$COOKIE_JAR" "$member_jar" "$OVERRIDES_FILE"; }
trap cleanup EXIT
rm -f "$OVERRIDES_FILE"

email="phaseg-$(date +%s)@example.com"
password="Password123!"
member_email="finance-member@example.com"

# Poll a job to a terminal state. Fails on failed/dead AND on timeout -- a
# poll loop that falls through silently turns a stuck job into a passing test.
wait_for_job() {
  local jid="$1" label="$2" i status=""
  for i in $(seq 1 30); do
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
    sleep 1
  done
  echo "$JOB"
  echo "FAIL: timed out waiting for $label (last status=$status)"
  exit 1
}

# HTTP status of GET /v1/documents/$1 as the group member.
member_doc_status() {
  curl -s -o /dev/null -w '%{http_code}' -b "$member_jar" "$API/v1/documents/$1"
}

# Poll GET /v1/documents/$1 (as the org owner) until status == ready, i.e. until
# the *child ingest* job has finished -- not just the parent Drive sync job.
# This matters: _upsert_drive_file's content-unchanged skip path requires the
# document to already be `ready`, so re-syncing before ingest lands would take
# the ordinary re-ingest path and the test would silently prove old behaviour.
wait_for_doc_ready() {
  local did="$1" label="$2" i status=""
  for i in $(seq 1 60); do
    status=$(curl -sf -b "$COOKIE_JAR" "$API/v1/documents/$did" |
      python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
    if [[ "$status" == "ready" ]]; then
      return 0
    fi
    if [[ "$status" == "failed" ]]; then
      curl -sf -b "$COOKIE_JAR" "$API/v1/documents/$did"
      echo "FAIL: $label -- document ingestion failed"
      exit 1
    fi
    sleep 1
  done
  echo "FAIL: timed out waiting for $label (last document status=$status)"
  exit 1
}

# Echoes 1 if the group member's /v1/search results contain document $1, else 0.
# Search reads ACL fields denormalised into OpenSearch at ingest time, so this
# is the only assertion that proves a revocation actually reached the index --
# Postgres going private is not enough to stop retrieval/RAG surfacing it.
member_search_hit() {
  curl -sf -b "$member_jar" -H 'Content-Type: application/json' \
    -d '{"query":"Finance Group Shared Report","limit":50}' "$API/v1/search" |
    DOC_ID="$1" python3 -c "
import json, os, sys
data = json.load(sys.stdin)
doc_id = os.environ['DOC_ID']
print(1 if any(r.get('document_id') == doc_id for r in (data.get('results') or [])) else 0)
"
}

echo "== register (becomes org owner/admin) =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase G\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase G Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== invite the finance group member (matches groups.py's MOCK_GROUP_MEMBERS fixture) =="
invite=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$member_email\",\"role\":\"member\"}" \
  "$API/v1/users/invite")
token=$(python3 -c "import json,sys; print(json.load(sys.stdin)['debug_token'])" <<<"$invite")
[[ -n "$token" && "$token" != "None" ]] || {
  echo "FAIL: no debug_token on invite. Run API with APP_ENV=development and EMAIL_PROVIDER=log"
  exit 1
}
curl -sf -c "$member_jar" -H 'Content-Type: application/json' \
  -d "{\"token\":\"$token\",\"name\":\"Finance Member\",\"password\":\"$password\"}" \
  "$API/v1/users/invite/accept" >/dev/null

echo "== submit fixture DWD key (mock mode) =="
body=$(python3 -c "
import json
fixture_key = {
    'type': 'service_account',
    'project_id': 'acme-proj',
    'private_key': '-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----\n',
    'client_email': 'highwatch-sync@acme-proj.iam.gserviceaccount.com',
    'client_id': '111111111111111111111',
}
print(json.dumps({'google_domain': 'acme.com', 'service_account_key': json.dumps(fixture_key)}))
")
curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' -d "$body" \
  "$API/v1/enterprise/google-workspace" | grep -q '"status":"verified"' || {
  echo "FAIL: DWD key did not verify"
  exit 1
}

echo "== connect Drive (mock OAuth round-trip) =="
hdrs="$(mktemp)"
curl -s -D "$hdrs" -o /dev/null -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
  "$API/v1/connections/google_drive/oauth/start"
loc=$(tr -d '\r' < "$hdrs" | awk 'tolower($1)=="location:"{print $2; exit}')
rm -f "$hdrs"
[[ -n "$loc" ]] || { echo "FAIL: missing OAuth Location header"; exit 1; }
curl -sf -o /dev/null -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$loc"
detail=$(curl -sf -b "$COOKIE_JAR" "$API/v1/connections/google_drive")
echo "$detail" | grep -q '"connected":true' || {
  echo "FAIL: Drive not connected after mock OAuth"
  echo "$detail"
  exit 1
}

echo "== sync #1: groups + Drive (folder-finance, org visibility) =="
job=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"folder_ids":["folder-finance"],"visibility":"org","selected_user_ids":[]}' \
  "$API/v1/connections/google_drive/sync")
job_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" <<<"$job")
wait_for_job "$job_id" "initial Drive sync"

echo "== find the group-shared document =="
doc_id=$(curl -sf -b "$COOKIE_JAR" "$API/v1/documents?limit=50" | python3 -c "
import json, sys
data = json.load(sys.stdin)
match = [d for d in data['items'] if d['title'] == 'Finance Group Shared Report']
assert match, 'fixture doc not found among: ' + repr([d['title'] for d in data['items']])
print(match[0]['id'])
")
echo "  doc_id=$doc_id"

echo "== group member CAN see the group-shared file =="
status_before=$(member_doc_status "$doc_id")
[[ "$status_before" == "200" ]] || {
  echo "FAIL: expected group member to see the group-shared doc (got HTTP $status_before)"
  exit 1
}

echo "== wait for the child ingest job (document -> ready) so sync #2 hits the skip path =="
wait_for_doc_ready "$doc_id" "initial ingest of the group-shared doc"

echo "== group member CAN find the group-shared file in search =="
hit_before=$(member_search_hit "$doc_id")
[[ "$hit_before" == "1" ]] || {
  echo "FAIL: expected group member to find the group-shared doc via /v1/search (got hit=$hit_before)"
  curl -sf -b "$member_jar" -H 'Content-Type: application/json' \
    -d '{"query":"Finance Group Shared Report","limit":50}' "$API/v1/search"
  exit 1
}

echo "== revoke the group share in the mock fixture (content/modifiedTime unchanged) =="
OVERRIDES_FILE_PY="$OVERRIDES_FILE_PY" FINANCE_GROUP_FILE_ID="$FINANCE_GROUP_FILE_ID" python3 -c "
import json, os
overrides = {
    'permissions': {
        os.environ['FINANCE_GROUP_FILE_ID']: [
            {'type': 'user', 'emailAddress': 'owner@example.com', 'role': 'owner'}
        ]
    }
}
with open(os.environ['OVERRIDES_FILE_PY'], 'w') as fh:
    json.dump(overrides, fh)
"

echo "== sync #2: re-sync after revoke =="
job2=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"folder_ids":["folder-finance"],"visibility":"org","selected_user_ids":[]}' \
  "$API/v1/connections/google_drive/sync")
job2_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" <<<"$job2")
wait_for_job "$job2_id" "re-sync after revoke"

echo "== group member can NO LONGER see the file (access revoked, content never changed) =="
status_after=$(member_doc_status "$doc_id")
[[ "$status_after" == "404" ]] || {
  echo "FAIL: expected group member to lose access after revoke+resync (got HTTP $status_after, expected 404)"
  exit 1
}

echo "== group member can NO LONGER find it in search (the revoke reached OpenSearch) =="
# The skip path updates Postgres synchronously but has to enqueue a re-ingest to
# rewrite the denormalised ACL fields in the index, so poll rather than assert
# once. The doc IS still in the member's search results at t=0 here (stale ACL),
# so this loop can only go green once that re-ingest has actually landed.
search_closed=0
for i in $(seq 1 60); do
  hit_after=$(member_search_hit "$doc_id")
  if [[ "$hit_after" == "0" ]]; then
    search_closed=1
    break
  fi
  sleep 1
done
[[ "$search_closed" == "1" ]] || {
  echo "FAIL: group member can still find the doc via /v1/search after revoke+resync --"
  echo "      the ACL recheck never reached the OpenSearch index (search/RAG still leaks it)"
  curl -sf -b "$member_jar" -H 'Content-Type: application/json' \
    -d '{"query":"Finance Group Shared Report","limit":50}' "$API/v1/search"
  exit 1
}

# Guard against a false pass: ingest deletes the document's OpenSearch docs
# *before* reindexing, so a re-ingest that crashed halfway would also make the
# member's search come back empty. Requiring the document back at `ready` (not
# `failed`) means the chunks really were rewritten with the new ACL.
echo "== the ACL-triggered re-ingest actually succeeded (document back to ready) =="
wait_for_doc_ready "$doc_id" "re-ingest triggered by the ACL change"

echo "== org owner (admin) can still see it regardless =="
owner_status=$(curl -s -o /dev/null -w '%{http_code}' -b "$COOKIE_JAR" "$API/v1/documents/$doc_id")
[[ "$owner_status" == "200" ]] || {
  echo "FAIL: expected admin to always see the doc (got HTTP $owner_status)"
  exit 1
}

echo "PHASE G SMOKE PASSED (group share granted access in both Postgres and search; revoke+resync closed both)"
