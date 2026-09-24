#!/usr/bin/env bash
# Phase J smoke (mock mode): Google Chat connector -- space picker, permission
# model (per-space membership -> per-thread DocumentGrant), full-relist
# deletion propagation (Chat has no checkpoint/history API), and auto-sync
# toggling.
#
# Requires the api+worker containers running with GOOGLE_CHAT_ENABLED=true
# and GOOGLE_CHAT_MODE=mock (docker-compose hardcodes oauth), e.g.:
#
#   GOOGLE_CHAT_ENABLED=true GOOGLE_CHAT_MODE=mock \
#     AUTO_SYNC_INTERVAL_SECONDS=5 AUTO_SYNC_TICK_SECONDS=2 \
#     docker compose -p vridhi up -d api worker
#   API_URL=http://localhost:8000 ./scripts/smoke-phase-j.sh
#
# Everything about access is asserted at the SEARCH level (what retrieval/RAG
# actually authorise against), not just database flags -- same convention as
# smoke-phase-g.sh and smoke-phase-h.sh.
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Mock-mode side channel (see services/chat.py); the api/worker containers
# bind-mount ./apps/api so host writes are visible to both.
CHAT_OVERRIDES="$REPO_ROOT/apps/api/.mock-chat-overrides.json"

# Native-Windows python3 (git-bash) does not understand /c/... paths.
py_path() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi
}
CHAT_OVERRIDES_PY="$(py_path "$CHAT_OVERRIDES")"

COOKIE_JAR="$(mktemp)"
member_jar="$(mktemp)"
outsider_jar="$(mktemp)"
cleanup() { rm -f "$COOKIE_JAR" "$member_jar" "$outsider_jar" "$CHAT_OVERRIDES"; }
trap cleanup EXIT
rm -f "$CHAT_OVERRIDES"

email="phasej-$(date +%s)@example.com"
password="Password123!"
member_email="finance-member@example.com"
outsider_email="phasej-outsider-$(date +%s)@example.com"

fail() { echo "FAIL: $*"; exit 1; }

write_json() { # <python-path> <json>
  python3 -c "import sys; open(sys.argv[1], 'w').write(sys.argv[2])" "$1" "$2"
}

api_get() { curl -sf -b "$COOKIE_JAR" "$API$1"; }

# poll <label> <timeout-seconds> <command...>: run the command every 2s until
# it succeeds; fail loudly on timeout (a silent fall-through would pass a
# stuck test) -- lesson from Piece 3's smoke scripts.
poll() {
  local label="$1" timeout="$2" waited=0
  shift 2
  until "$@"; do
    waited=$((waited + 2))
    [[ $waited -lt $timeout ]] || fail "timed out after ${timeout}s waiting for: $label"
    sleep 2
  done
}

# wait_for_job <job_id>: poll GET /v1/jobs/{id} to a terminal state. Fails on
# failed/dead AND on timeout.
wait_for_job() {
  local jid="$1" label="$2" i status=""
  for i in $(seq 1 60); do
    job=$(curl -sf -b "$COOKIE_JAR" "$API/v1/jobs/$jid")
    status=$(python3 -c "import json,sys; print(json.load(sys.stdin)['status'])" <<<"$job")
    if [[ "$status" == "succeeded" ]]; then
      return 0
    fi
    if [[ "$status" == "dead" || "$status" == "failed" ]]; then
      echo "$job"
      fail "$label ended in $status"
    fi
    sleep 2
  done
  echo "$job"
  fail "timed out waiting for $label (last status=$status)"
}

start_chat_sync() { # <incremental true|false> -> echoes job id
  curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
    -d "{\"incremental\":$1}" "$API/v1/connections/google_chat/sync" |
    python3 -c "import json,sys; print(json.load(sys.stdin)['id'])"
}

# search_hit_by_title <jar> <query> <title-prefix> -> prints 1 if that
# session's /v1/search results contain a document whose title starts with
# <title-prefix>. thread_title() (services/chat_threads.py) always prefixes
# the title with the space's display name ("Finance — ..." / "Engineering —
# ..."), so the prefix alone is enough to identify the budget thread's
# document without depending on the (truncated) message-text snippet.
search_hit_by_title() { # <jar> <query> <title-prefix>
  curl -sf -b "$1" -H 'Content-Type: application/json' \
    -d "{\"query\":\"$2\",\"limit\":50}" "$API/v1/search" |
    PREFIX="$3" python3 -c "
import json, os, sys
data = json.load(sys.stdin)
prefix = os.environ['PREFIX']
print(1 if any((r.get('title') or '').startswith(prefix) for r in (data.get('results') or [])) else 0)
"
}
BUDGET_QUERY="Q4 budget review"
BUDGET_PREFIX="Finance"
STANDUP_QUERY="daily standup Chat connector"
STANDUP_PREFIX="Engineering"
budget_visible_member() { [[ "$(search_hit_by_title "$member_jar" "$BUDGET_QUERY" "$BUDGET_PREFIX")" == "1" ]]; }
budget_gone_member() { [[ "$(search_hit_by_title "$member_jar" "$BUDGET_QUERY" "$BUDGET_PREFIX")" == "0" ]]; }

# scheduled_job_count <status-regex>: automatic (schedule-triggered) chat jobs
# whose status matches.
scheduled_job_count() {
  api_get "/v1/connections/google_chat/syncs?limit=100" | STATUS="$1" python3 -c "
import json, os, re, sys
items = json.load(sys.stdin)['items']
pat = os.environ['STATUS']
print(sum(1 for j in items if j.get('trigger') == 'schedule' and re.fullmatch(pat, j['status'])))
"
}

echo "== register (becomes org owner/admin) =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase J\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase J Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== invite the finance space member (matches services/chat.py's MOCK_MEMBERS fixture) =="
invite=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$member_email\",\"role\":\"member\"}" \
  "$API/v1/users/invite")
token=$(python3 -c "import json,sys; print(json.load(sys.stdin)['debug_token'])" <<<"$invite")
[[ -n "$token" && "$token" != "None" ]] || fail "no debug_token on invite. Run API with APP_ENV=development and EMAIL_PROVIDER=log"
curl -sf -c "$member_jar" -H 'Content-Type: application/json' \
  -d "{\"token\":\"$token\",\"name\":\"Finance Member\",\"password\":\"$password\"}" \
  "$API/v1/users/invite/accept" >/dev/null

echo "== invite a second, non-member org member (has no space membership at all) =="
invite2=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$outsider_email\",\"role\":\"member\"}" \
  "$API/v1/users/invite")
token2=$(python3 -c "import json,sys; print(json.load(sys.stdin)['debug_token'])" <<<"$invite2")
[[ -n "$token2" && "$token2" != "None" ]] || fail "no debug_token on second invite"
curl -sf -c "$outsider_jar" -H 'Content-Type: application/json' \
  -d "{\"token\":\"$token2\",\"name\":\"Outsider Member\",\"password\":\"$password\"}" \
  "$API/v1/users/invite/accept" >/dev/null

echo "== connect Google Chat (mock OAuth round-trip) =="
# Two hops: oauth/start redirects to the mock oauth/callback URL (chat.py's
# oauth_start_url, mock mode), and THAT redirects to the frontend with
# ?chat=connected (chat_oauth_callback in routers/chat.py). Capture headers on
# both hops so the ?chat=connected assertion is against the real final Location.
hdrs="$(mktemp)"
curl -s -D "$hdrs" -o /dev/null -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
  "$API/v1/connections/google_chat/oauth/start"
oauth_start_path=$(tr -d '\r' <"$hdrs" | awk 'tolower($1)=="location:"{print $2; exit}')
[[ -n "$oauth_start_path" ]] || fail "missing OAuth Location header for google_chat oauth/start"
curl -s -D "$hdrs" -o /dev/null -b "$COOKIE_JAR" -c "$COOKIE_JAR" "$oauth_start_path"
callback_loc=$(tr -d '\r' <"$hdrs" | awk 'tolower($1)=="location:"{print $2; exit}')
rm -f "$hdrs"
[[ -n "$callback_loc" ]] || fail "missing OAuth Location header for google_chat oauth/callback"
[[ "$callback_loc" == *"?chat=connected"* ]] \
  || fail "oauth_start_path redirect did not land on ?chat=connected (got: $callback_loc)"
api_get "/v1/connections/google_chat" | grep -q '"connected":true' || fail "google_chat not connected after mock OAuth"

echo "== list spaces: both mock spaces are present =="
spaces_json=$(api_get "/v1/connections/google_chat/spaces")
echo "$spaces_json" | grep -q '"spaces/mockspace-finance"' || fail "spaces/mockspace-finance missing from space list"
echo "$spaces_json" | grep -q '"spaces/mockspace-eng"' || fail "spaces/mockspace-eng missing from space list"

echo "== select only the Finance space =="
curl -sf -X PUT -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"space_ids":["spaces/mockspace-finance"]}' \
  "$API/v1/connections/google_chat/spaces" | grep -q '"spaces/mockspace-finance"' \
  || fail "PUT spaces did not persist selection"

echo "== sync #1 (full relist) =="
job1_id=$(start_chat_sync false)
wait_for_job "$job1_id" "initial Chat sync"

echo "== the budget thread is searchable by the finance member, and NOT by a non-member =="
poll "budget thread visible to finance member" 60 budget_visible_member
[[ "$(search_hit_by_title "$outsider_jar" "$BUDGET_QUERY" "$BUDGET_PREFIX")" == "0" ]] \
  || fail "the budget thread leaked to a non-member org user -- the core permission-model assertion failed"

echo "== simulate the two budget messages being deleted in Google Chat =="
write_json "$CHAT_OVERRIDES_PY" '{"removed_message_ids":["msg-budget-001","msg-budget-002"]}'

echo "== sync #2 (full relist -- Chat has no checkpoint/history API, so deletion is only caught this way) =="
job2_id=$(start_chat_sync false)
wait_for_job "$job2_id" "re-sync after message deletion"

echo "== the budget thread document is no longer returned by search for the finance member =="
poll "budget thread gone from search after deletion" 60 budget_gone_member

echo "== unselect the Finance space entirely (space_ids: []) =="
curl -sf -X PUT -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"space_ids":[]}' \
  "$API/v1/connections/google_chat/spaces" >/dev/null

echo "== with nothing selected, sync refuses to run (SPACES_REQUIRED) -- nothing can leak in =="
no_space_status=$(curl -s -o /dev/null -w '%{http_code}' -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"incremental":false}' "$API/v1/connections/google_chat/sync")
[[ "$no_space_status" == "400" ]] || fail "expected 400 SPACES_REQUIRED syncing with no spaces selected (got $no_space_status)"

echo "== the (already-tombstoned) budget thread is still gone, for both the finance member and the outsider =="
budget_gone_member || fail "budget thread reappeared in search after unselecting Finance"
[[ "$(search_hit_by_title "$outsider_jar" "$BUDGET_QUERY" "$BUDGET_PREFIX")" == "0" ]] \
  || fail "budget thread reappeared in search for a non-member after unselecting Finance"

echo "== even explicitly syncing the never-selected Engineering space produces nothing visible to anyone =="
# The Engineering space has no members in MOCK_MEMBERS (services/chat.py), so
# its standup thread must never be searchable -- proving the Engineering-space
# setup did not leak into search via any path.
# NOTE: this sync also calls ChatService.start_sync with space_ids explicitly
# set, which persists selected_space_ids=["spaces/mockspace-eng"] on the
# connection (start_sync always writes the selection it was given back to
# conn.config). The auto-sync-resume check further down relies on this: it
# expects the scheduler to have a selected space to pick up once auto-sync is
# switched back on, and this is the step that leaves one selected.
job3_id=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"space_ids":["spaces/mockspace-eng"],"incremental":false}' \
  "$API/v1/connections/google_chat/sync" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
wait_for_job "$job3_id" "sync with only the Engineering space selected"
[[ "$(search_hit_by_title "$member_jar" "$STANDUP_QUERY" "$STANDUP_PREFIX")" == "0" ]] \
  || fail "Engineering-space standup thread leaked into search for the finance member despite having no resolvable members"
[[ "$(search_hit_by_title "$outsider_jar" "$STANDUP_QUERY" "$STANDUP_PREFIX")" == "0" ]] \
  || fail "Engineering-space standup thread leaked into search for the outsider despite having no resolvable members"

echo "== switching auto-sync off stops automatic syncs =="
curl -sf -X PUT -b "$COOKIE_JAR" -H 'Content-Type: application/json' -d '{"enabled":false}' \
  "$API/v1/connections/google_chat/auto-sync" | grep -q '"auto_sync_enabled":false' \
  || fail "switch-off did not stick"
sleep 15
off_count="$(scheduled_job_count '.*')"
sleep 40
[[ "$(scheduled_job_count '.*')" == "$off_count" ]] \
  || fail "automatic Chat syncs kept running while auto-sync was off"

echo "== switching auto-sync back on resumes automatic syncs =="
curl -sf -X PUT -b "$COOKIE_JAR" -H 'Content-Type: application/json' -d '{"enabled":true}' \
  "$API/v1/connections/google_chat/auto-sync" | grep -q '"auto_sync_enabled":true' \
  || fail "switch-on did not stick"
resumed_after_off() { [[ "$(scheduled_job_count '.*')" -gt "$off_count" ]]; }
poll "an automatic Chat sync resumes" 150 resumed_after_off

echo "PHASE J SMOKE PASSED"
