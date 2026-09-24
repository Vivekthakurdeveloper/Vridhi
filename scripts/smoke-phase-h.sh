#!/usr/bin/env bash
# Phase H smoke (mock mode): automatic sync + deletion propagation.
#
# Requires the worker to run with a short schedule and BOTH connectors in mock
# mode (docker-compose.yml hardcodes oauth; flip GOOGLE_DRIVE_MODE and GMAIL_MODE
# to mock on api+worker first):
#
#   AUTO_SYNC_INTERVAL_SECONDS=5 AUTO_SYNC_TICK_SECONDS=2 \
#     docker compose -p vridhi up -d worker
#   API_URL=http://localhost:8000 ./scripts/smoke-phase-h.sh
#
# Everything is asserted at the SEARCH level (what retrieval/RAG actually
# authorise against), not just database flags.
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Mock-mode side channels (see services/drive.py and services/gmail.py); the
# api/worker containers bind-mount ./apps/api so host writes are visible to both.
DRIVE_OVERRIDES="$REPO_ROOT/apps/api/.mock-drive-overrides.json"
GMAIL_OVERRIDES="$REPO_ROOT/apps/api/.mock-gmail-overrides.json"

# Native-Windows python3 (git-bash) does not understand /c/... paths.
py_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}
DRIVE_OVERRIDES_PY="$(py_path "$DRIVE_OVERRIDES")"
GMAIL_OVERRIDES_PY="$(py_path "$GMAIL_OVERRIDES")"

COOKIE_JAR="$(mktemp)"
cleanup() { rm -f "$COOKIE_JAR" "$DRIVE_OVERRIDES" "$GMAIL_OVERRIDES"; }
trap cleanup EXIT
rm -f "$DRIVE_OVERRIDES" "$GMAIL_OVERRIDES"

email="phaseh-$(date +%s)@example.com"
password="Password123!"

fail() { echo "FAIL: $*"; exit 1; }

# poll <label> <timeout-seconds> <command...>: run the command every 2s until it
# succeeds; fail loudly on timeout (a silent fall-through would pass a stuck test).
poll() {
  local label="$1" timeout="$2" waited=0
  shift 2
  until "$@"; do
    waited=$((waited + 2))
    [[ $waited -lt $timeout ]] || fail "timed out after ${timeout}s waiting for: $label"
    sleep 2
  done
}

api_get() { curl -sf -b "$COOKIE_JAR" "$API$1"; }

write_json() { # <python-path> <json>
  python3 -c "import sys; open(sys.argv[1], 'w').write(sys.argv[2])" "$1" "$2"
}

mock_oauth_connect() { # <drive: google_drive | gmail>
  local connector="$1" hdrs loc
  hdrs="$(mktemp)"
  curl -s -D "$hdrs" -o /dev/null -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
    "$API/v1/connections/$connector/oauth/start"
  loc=$(tr -d '\r' <"$hdrs" | awk 'tolower($1)=="location:"{print $2; exit}')
  rm -f "$hdrs"
  [[ -n "$loc" ]] || fail "missing OAuth Location header for $connector"
  curl -s -o /dev/null -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$loc"
  api_get "/v1/connections/$connector" | grep -q '"connected":true' \
    || fail "$connector not connected after mock OAuth"
}

# doc_id_by_title <source> <title-prefix>: prints the document id, or nothing.
doc_id_by_title() {
  api_get "/v1/documents?limit=100" | SRC="$1" PREFIX="$2" python3 -c "
import json, os, sys
items = json.load(sys.stdin)['items']
m = [d for d in items if d.get('source') == os.environ['SRC'] and d['title'].startswith(os.environ['PREFIX'])]
print(m[0]['id'] if m else '')
"
}

# search_hit <query> <doc_id>: prints 1 if the admin's /v1/search results contain the document.
search_hit() {
  curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
    -d "{\"query\":\"$1\",\"limit\":50}" "$API/v1/search" |
    DOC_ID="$2" python3 -c "
import json, os, sys
data = json.load(sys.stdin)
print(1 if any(r.get('document_id') == os.environ['DOC_ID'] for r in (data.get('results') or [])) else 0)
"
}
found_in_search() { [[ "$(search_hit "$1" "$2")" == "1" ]]; }
gone_from_search() { [[ "$(search_hit "$1" "$2")" == "0" ]]; }
doc_ready() {
  [[ "$(api_get "/v1/documents/$1" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")" == "ready" ]]
}
doc_http_status() { curl -s -o /dev/null -w '%{http_code}' -b "$COOKIE_JAR" "$API/v1/documents/$1"; }
doc_is_404() { [[ "$(doc_http_status "$1")" == "404" ]]; }
# has_doc <source> <title-prefix>: succeeds once such a (non-deleted) document exists.
# Used with poll; a "$(...)" argument to poll would be evaluated only once, up front.
has_doc() { [[ -n "$(doc_id_by_title "$1" "$2")" ]]; }

# scheduled_job_count <connector> <status-regex>: Automatic jobs whose status matches.
scheduled_job_count() {
  api_get "/v1/connections/$1/syncs?limit=100" | STATUS="$2" python3 -c "
import json, os, re, sys
items = json.load(sys.stdin)['items']
pat = os.environ['STATUS']
print(sum(1 for j in items if j.get('trigger') == 'schedule' and re.fullmatch(pat, j['status'])))
"
}
has_scheduled_jobs() { [[ "$(scheduled_job_count "$1" "$2")" -ge "$3" ]]; }

# failed_job_mentions_outage: succeeds if some Automatic Drive job that failed
# (or died) recorded the mock listing outage as its error, proving the outage --
# not some unrelated failure -- is what we are observing.
failed_job_mentions_outage() {
  [[ "$(api_get "/v1/connections/google_drive/syncs?limit=100" | python3 -c "
import json, re, sys
items = json.load(sys.stdin)['items']
print(1 if any(
    j.get('trigger') == 'schedule'
    and re.fullmatch('failed|dead', j['status'])
    and 'fail_listing' in (j.get('error_message') or '')
    for j in items
) else 0)
")" == "1" ]]
}

echo "== register (org owner/admin) =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase H\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase H Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== connect Drive + Gmail (mock OAuth) and choose Drive folders =="
mock_oauth_connect google_drive
mock_oauth_connect gmail
curl -sf -X PUT -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"folder_ids":["folder-finance","folder-hr"]}' \
  "$API/v1/connections/google_drive/folders" >/dev/null

echo "== an AUTOMATIC Drive sync and an AUTOMATIC Gmail sync run with no click =="
poll "automatic Drive sync (worker needs AUTO_SYNC_INTERVAL_SECONDS=5 AUTO_SYNC_TICK_SECONDS=2)" 150 \
  has_scheduled_jobs google_drive 'succeeded' 1
poll "automatic Gmail sync" 150 has_scheduled_jobs gmail 'succeeded' 1

echo "== synced documents become searchable =="
poll "GST doc synced" 60 has_doc google_drive 'GST Invoice SOP'
gst_id="$(doc_id_by_title google_drive 'GST Invoice SOP')"
leave_id="$(doc_id_by_title google_drive 'Leave Policy')"
[[ -n "$gst_id" && -n "$leave_id" ]] || fail "expected Drive docs GST Invoice SOP and Leave Policy"
poll "GST doc ready" 90 doc_ready "$gst_id"
poll "Leave doc ready" 90 doc_ready "$leave_id"
poll "GST doc in search" 60 found_in_search "GST invoice must be issued within 7 days" "$gst_id"
poll "Leave doc in search" 60 found_in_search "Leave requests require manager approval" "$leave_id"

echo "== a file removed in Google disappears from search (and comes back when restored) =="
write_json "$DRIVE_OVERRIDES_PY" '{"removed":["file-gst-sop"]}'
poll "GST doc gone from search" 150 gone_from_search "GST invoice must be issued within 7 days" "$gst_id"
poll "GST doc hidden in the API" 30 doc_is_404 "$gst_id"
rm -f "$DRIVE_OVERRIDES"
poll "GST doc restored" 150 has_doc google_drive 'GST Invoice SOP'
restored_id="$(doc_id_by_title google_drive 'GST Invoice SOP')"
[[ "$restored_id" == "$gst_id" ]] || fail "restore created a different document ($restored_id != $gst_id)"
poll "restored GST doc ready" 90 doc_ready "$gst_id"
poll "restored GST doc in search" 60 found_in_search "GST invoice must be issued within 7 days" "$gst_id"

echo "== Vridhi's own Delete button removes it from search, and it stays deleted across automatic syncs =="
curl -sf -X DELETE -b "$COOKIE_JAR" "$API/v1/documents/$leave_id" >/dev/null || fail "delete request failed"
poll "Leave doc gone from search after Delete" 30 gone_from_search "Leave requests require manager approval" "$leave_id"
before_jobs="$(scheduled_job_count google_drive '.*')"
poll "two more automatic Drive syncs" 150 has_scheduled_jobs google_drive '.*' "$((before_jobs + 2))"
poll "latest automatic sync finished" 60 has_scheduled_jobs google_drive 'succeeded' "$((before_jobs + 1))"
[[ -z "$(doc_id_by_title google_drive 'Leave Policy')" ]] \
  || fail "a manually deleted document was re-imported by an automatic sync"
gone_from_search "Leave requests require manager approval" "$leave_id" \
  || fail "a manually deleted document is searchable again after automatic syncs"

echo "== a Gmail message deleted in Google: its attachments disappear from search =="
poll "Gmail invoice doc synced" 60 has_doc gmail 'invoice-INV-2024-0912'
inv_id="$(doc_id_by_title gmail 'invoice-INV-2024-0912')"
poll "Gmail invoice doc ready" 90 doc_ready "$inv_id"
poll "Gmail invoice doc in search" 60 found_in_search "Invoice INV-2024-0912" "$inv_id"
write_json "$GMAIL_OVERRIDES_PY" \
  '{"history_id":"1001","history":[{"id":"1001","messagesDeleted":[{"message":{"id":"msg-invoice-001"}}]}],"removed_message_ids":["msg-invoice-001"]}'
poll "Gmail invoice doc gone from search" 150 gone_from_search "Invoice INV-2024-0912" "$inv_id"
poll "Gmail invoice doc hidden in the API" 30 doc_is_404 "$inv_id"

echo "== a Drive outage hides nothing =="
fail_before="$(scheduled_job_count google_drive 'failed|dead')"
write_json "$DRIVE_OVERRIDES_PY" '{"fail_listing":true}'
poll "a NEW automatic Drive sync fails" 180 has_scheduled_jobs google_drive 'failed|dead' "$((fail_before + 1))"
poll "the failed sync reports the listing outage" 60 failed_job_mentions_outage
[[ "$(search_hit "GST invoice must be issued within 7 days" "$gst_id")" == "1" ]] \
  || fail "a failed listing removed a document from search"
[[ "$(doc_http_status "$gst_id")" == "200" ]] || fail "a failed listing hid a document"
rm -f "$DRIVE_OVERRIDES"

echo "== switching auto-sync off stops automatic syncs; on resumes them =="
curl -sf -X PUT -b "$COOKIE_JAR" -H 'Content-Type: application/json' -d '{"enabled":false}' \
  "$API/v1/connections/google_drive/auto-sync" | grep -q '"auto_sync_enabled":false' \
  || fail "switch-off did not stick"
sleep 15
off_count="$(scheduled_job_count google_drive '.*')"
sleep 40
[[ "$(scheduled_job_count google_drive '.*')" == "$off_count" ]] \
  || fail "automatic syncs kept running while auto-sync was off"
curl -sf -X PUT -b "$COOKIE_JAR" -H 'Content-Type: application/json' -d '{"enabled":true}' \
  "$API/v1/connections/google_drive/auto-sync" | grep -q '"auto_sync_enabled":true' \
  || fail "switch-on did not stick"
poll "automatic syncs resume" 150 has_scheduled_jobs google_drive '.*' "$((off_count + 1))"

echo "PHASE H SMOKE PASSED"
