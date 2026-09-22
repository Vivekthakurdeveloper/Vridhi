#!/usr/bin/env bash
# Phase I smoke: ZIP expansion, old Office formats, multi-tab Sheets, Shared Drives.
# Mock mode only. Assumes the `vridhi` compose stack is running with
# GOOGLE_DRIVE_MODE=mock. Mirrors scripts/smoke-phase-h.sh's helpers (base URL,
# auth, poll()/has_doc()) and scripts/smoke-phase-g.sh's manual-sync pattern
# (POST .../sync + poll /v1/jobs/{id}), so this does not depend on Piece 3's
# scheduler timing.
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- copied verbatim from scripts/smoke-phase-h.sh's preamble -----------------

# Mock-mode side channel (see services/drive.py); the api/worker containers
# bind-mount ./apps/api so host writes are visible to both.
DRIVE_OVERRIDES="$REPO_ROOT/apps/api/.mock-drive-overrides.json"

# Native-Windows python3 (git-bash) does not understand /c/... paths.
py_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}
DRIVE_OVERRIDES_PY="$(py_path "$DRIVE_OVERRIDES")"

COOKIE_JAR="$(mktemp)"
cleanup() { rm -f "$COOKIE_JAR" "$DRIVE_OVERRIDES"; }
trap cleanup EXIT
rm -f "$DRIVE_OVERRIDES"

email="phasei-$(date +%s)@example.com"
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
# has_doc <source> <title-prefix>: succeeds once such a (non-deleted) document exists.
has_doc() { [[ -n "$(doc_id_by_title "$1" "$2")" ]]; }

# --- end of copied preamble ----------------------------------------------------

# run_sync <folder-ids-json-array>: manual "Sync Now" (POST .../sync), waits for
# the job's terminal state via /v1/jobs/{id} -- avoids depending on Piece 3's
# scheduler timing (see smoke-phase-g.sh, same pattern).
run_sync() {
  local folder_ids_json="$1" job job_id status i
  job=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
    -d "{\"folder_ids\":$folder_ids_json,\"visibility\":\"org\",\"selected_user_ids\":[]}" \
    "$API/v1/connections/google_drive/sync")
  job_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['id'])" <<<"$job")
  for i in $(seq 1 60); do
    status=$(api_get "/v1/jobs/$job_id" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
    [[ "$status" == "succeeded" ]] && return 0
    [[ "$status" == "failed" || "$status" == "dead" ]] && {
      api_get "/v1/jobs/$job_id"
      fail "Drive sync job ended in $status"
    }
    sleep 2
  done
  fail "timed out waiting for Drive sync job $job_id"
}

echo "== register (org owner/admin) =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase I\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase I Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== connect Drive (mock OAuth) and select all fixture folders =="
mock_oauth_connect google_drive
curl -sf -X PUT -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"folder_ids":["folder-contracts","folder-hr","folder-finance","folder-shared-legal"]}' \
  "$API/v1/connections/google_drive/folders" >/dev/null

echo "== sync #1: everything (ZIP, legacy .doc, multi-tab Sheet, Shared Drive) =="
run_sync '["folder-contracts","folder-hr","folder-finance","folder-shared-legal"]'

echo "=== Scenario 1: ZIP inner files are searchable, cited by inner name ==="
poll "ZIP inner Leave.txt doc synced" 60 has_doc google_drive 'Reports Bundle.zip / Reports/Leave'
leave_zip_id="$(doc_id_by_title google_drive 'Reports Bundle.zip / Reports/Leave')"
poll "ZIP inner NDA.txt doc synced" 60 has_doc google_drive 'Reports Bundle.zip / Contracts/NDA'
nda_zip_id="$(doc_id_by_title google_drive 'Reports Bundle.zip / Contracts/NDA')"
poll "ZIP inner Leave.txt ready" 90 doc_ready "$leave_zip_id"
poll "ZIP inner NDA.txt ready" 90 doc_ready "$nda_zip_id"
poll "ZIP inner Leave.txt in search, cited by inner path" 60 \
  found_in_search "ZIPTEST-LEAVE-9F3" "$leave_zip_id"
poll "ZIP inner NDA.txt in search, cited by inner path" 60 \
  found_in_search "ZIPTEST-NDA-7K2" "$nda_zip_id"
title_of_leave="$(api_get "/v1/documents/$leave_zip_id" | python3 -c "import json,sys; print(json.load(sys.stdin)['title'])")"
[[ "$title_of_leave" == *"Reports/Leave"* ]] \
  || fail "expected ZIP inner document title to contain its inner path, got: $title_of_leave"
echo "Scenario 1 passed."

echo "=== Scenario 2: removing an inner file / whole ZIP hides it from search ==="
echo "-- whole-ZIP removal (removed-list override) --"
write_json "$DRIVE_OVERRIDES_PY" '{"removed":["file-mock-zip"]}'
run_sync '["folder-contracts","folder-hr","folder-finance","folder-shared-legal"]'
poll "ZIP inner Leave.txt gone from search after whole-zip removal" 60 \
  gone_from_search "ZIPTEST-LEAVE-9F3" "$leave_zip_id"
poll "ZIP inner NDA.txt gone from search after whole-zip removal" 60 \
  gone_from_search "ZIPTEST-NDA-7K2" "$nda_zip_id"
rm -f "$DRIVE_OVERRIDES"

echo "-- restore, then the lighter case: one inner file dropped from the same zip --"
run_sync '["folder-contracts","folder-hr","folder-finance","folder-shared-legal"]'
poll "ZIP inner Leave.txt ready again" 90 doc_ready "$leave_zip_id"
poll "ZIP inner NDA.txt ready again" 90 doc_ready "$nda_zip_id"
poll "ZIP inner Leave.txt restored" 60 found_in_search "ZIPTEST-LEAVE-9F3" "$leave_zip_id"
poll "ZIP inner NDA.txt restored" 60 found_in_search "ZIPTEST-NDA-7K2" "$nda_zip_id"
# Swap the zip's content_b64 in place (content_b64 override -- see get_mock_files())
# to a version of the same archive missing Contracts/NDA.txt.
write_json "$DRIVE_OVERRIDES_PY" '{"content_b64":{"file-mock-zip":"UEsDBBQAAAAAAKw8Nl1kPmpyXAAAAFwAAAARAAAAUmVwb3J0cy9MZWF2ZS50eHRaSVAgaW5uZXIgTGVhdmUgZG9jLgpFbXBsb3llZXMgZ2V0IDIxIGRheXMgYW5udWFsIGxlYXZlIHVuZGVyIHRoZSBaSVBURVNULUxFQVZFLTlGMyBwb2xpY3kuClBLAQIUAxQAAAAAAKw8Nl1kPmpyXAAAAFwAAAARAAAAAAAAAAAAAACAAQAAAABSZXBvcnRzL0xlYXZlLnR4dFBLBQYAAAAAAQABAD8AAACLAAAAAAA="}}'
run_sync '["folder-contracts","folder-hr","folder-finance","folder-shared-legal"]'
poll "ZIP inner NDA.txt gone after the zip shrank (single-file removal)" 60 \
  gone_from_search "ZIPTEST-NDA-7K2" "$nda_zip_id"
found_in_search "ZIPTEST-LEAVE-9F3" "$leave_zip_id" \
  || fail "the zip's other inner file should still be searchable after a partial shrink"
rm -f "$DRIVE_OVERRIDES"
echo "Scenario 2 passed."

echo "=== Scenario 3: a Shared Drive file is searchable ==="
poll "NDA Template (Shared Drive) doc synced" 60 has_doc google_drive 'NDA Template'
nda_template_id="$(doc_id_by_title google_drive 'NDA Template')"
poll "NDA Template ready" 90 doc_ready "$nda_template_id"
poll "NDA Template in search" 60 found_in_search "mutual non-disclosure" "$nda_template_id"
echo "Scenario 3 passed."

echo "=== Scenario 4: an old .doc file is searchable ==="
poll "Legacy Memo.doc synced" 60 has_doc google_drive 'Legacy Memo'
legacy_id="$(doc_id_by_title google_drive 'Legacy Memo')"
poll "Legacy Memo.doc ready (catdoc ran)" 90 doc_ready "$legacy_id"
poll "Legacy Memo.doc in search" 60 found_in_search "CATDOCMARKER-7731" "$legacy_id"
echo "Scenario 4 passed."

echo "=== Scenario 5: a second Sheet tab is searchable ==="
poll "Quarterly Tabs.xlsx synced" 60 has_doc google_drive 'Quarterly Tabs'
tabs_id="$(doc_id_by_title google_drive 'Quarterly Tabs')"
poll "Quarterly Tabs.xlsx ready" 90 doc_ready "$tabs_id"
poll "second tab (Details) text in search" 60 found_in_search "XLSXTAB2-BRAVO-224" "$tabs_id"
echo "Scenario 5 passed."

echo "All Phase I smoke scenarios passed."
