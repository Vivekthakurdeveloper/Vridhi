#!/usr/bin/env bash
# Phase 1 true customer journey E2E (API-level). Requires compose API+worker healthy.
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$ROOT/scripts/fixtures"
COOKIE_A="$(mktemp)"
COOKIE_B="$(mktemp)"
STAMP="$(date +%s)"
OWNER_EMAIL="p1-owner-${STAMP}@example.com"
MEMBER_EMAIL="p1-member-${STAMP}@example.com"
PASSWORD="Secret123!"
trap 'rm -f "$COOKIE_A" "$COOKIE_B"' EXIT

step() { echo ""; echo "==> $*"; }
pass() { echo "    PASS: $*"; }
fail() { echo "    FAIL: $*"; exit 1; }

json() {
  python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"
}

wait_ready() {
  local doc_id="$1"
  local ready=0
  for i in $(seq 1 60); do
    st=$(curl -sf -b "$COOKIE_A" "$API/v1/documents/$doc_id/status" | json "d.get('status')")
    echo "      poll $i status=$st"
    if [[ "$st" == "ready" ]]; then ready=1; break; fi
    if [[ "$st" == "failed" ]]; then
      curl -sf -b "$COOKIE_A" "$API/v1/documents/$doc_id" || true
      fail "document $doc_id failed"
    fi
    sleep 2
  done
  [[ "$ready" == "1" ]] || fail "timeout waiting for $doc_id"
}

echo "PHASE 1 E2E against $API"
step "1-5 health + signup + verify email + login"
curl -sf "$API/healthz" >/dev/null
reg=$(curl -sf -c "$COOKIE_A" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Owner ${STAMP}\",\"email\":\"${OWNER_EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"P1 Co ${STAMP}\"}" \
  "$API/v1/auth/register")
VERIFY=$(echo "$reg" | json "d.get('debug_verify_token') or ''")
[[ -n "$VERIFY" ]] || fail "debug_verify_token missing (need APP_ENV=development EMAIL_PROVIDER=log)"
curl -sf -H 'Content-Type: application/json' -d "{\"token\":\"$VERIFY\"}" "$API/v1/auth/verify-email" >/dev/null
curl -sf -b "$COOKIE_A" -c "$COOKIE_A" -X POST "$API/v1/auth/logout" >/dev/null
curl -sf -c "$COOKIE_A" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${OWNER_EMAIL}\",\"password\":\"${PASSWORD}\"}" \
  "$API/v1/auth/login" >/dev/null
pass "signup/verify/login"

step "6-8 invite member + logout/login"
inv=$(curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${MEMBER_EMAIL}\",\"role\":\"member\"}" "$API/v1/users/invite")
INV_TOKEN=$(echo "$inv" | json "d['debug_token']")
curl -sf -b "$COOKIE_A" -c "$COOKIE_A" -X POST "$API/v1/auth/logout" >/dev/null
curl -sf -c "$COOKIE_B" -H 'Content-Type: application/json' \
  -d "{\"token\":\"${INV_TOKEN}\",\"name\":\"Member ${STAMP}\",\"password\":\"${PASSWORD}\"}" \
  "$API/v1/users/invite/accept" >/dev/null
curl -sf -b "$COOKIE_B" -c "$COOKIE_B" -X POST "$API/v1/auth/logout" >/dev/null
curl -sf -c "$COOKIE_B" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${MEMBER_EMAIL}\",\"password\":\"${PASSWORD}\"}" \
  "$API/v1/auth/login" >/dev/null
curl -sf -c "$COOKIE_A" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${OWNER_EMAIL}\",\"password\":\"${PASSWORD}\"}" \
  "$API/v1/auth/login" >/dev/null
pass "invite + dual login"

step "9-14 Knowledge uploads"
DOC_TXT=$(curl -sf -b "$COOKIE_A" -F "file=@$FIX/policy.txt;type=text/plain" -F "visibility=org" "$API/v1/documents/upload" | json "d['document']['id']")
DOC_PDF=$(curl -sf -b "$COOKIE_A" -F "file=@$FIX/policy.pdf;type=application/pdf" -F "visibility=org" "$API/v1/documents/upload" | json "d['document']['id']")
DOC_DOCX=$(curl -sf -b "$COOKIE_A" -F "file=@$FIX/policy.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document" -F "visibility=org" "$API/v1/documents/upload" | json "d['document']['id']")
DOC_XLSX=$(curl -sf -b "$COOKIE_A" -F "file=@$FIX/pricing.xlsx;type=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" -F "visibility=org" "$API/v1/documents/upload" | json "d['document']['id']")
DOC_PPTX=$(curl -sf -b "$COOKIE_A" -F "file=@$FIX/deck.pptx;type=application/vnd.openxmlformats-officedocument.presentationml.presentation" -F "visibility=org" "$API/v1/documents/upload" | json "d['document']['id']")
for id in "$DOC_TXT" "$DOC_PDF" "$DOC_DOCX" "$DOC_XLSX" "$DOC_PPTX"; do wait_ready "$id"; done
pass "TXT/PDF/DOCX/XLSX/PPTX ready"

step "15-16 search"
search=$(curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -d '{"query":"18 days paid leave","limit":5}' "$API/v1/search")
echo "$search" | grep -qi "leave\|18" || fail "search missing leave content"
pass "search passages"

step "17-24 ask + citations + no-answer + feedback"
chat=$(curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -d '{"query":"How many paid leave days do employees get?","stream":false}' "$API/v1/chat")
echo "$chat" | grep -qi "18" || fail "chat missing grounded leave answer"
MSG=$(echo "$chat" | json "d['assistant_message']['id']")
CITS=$(echo "$chat" | json "len(d['assistant_message'].get('citations') or [])")
[[ "$CITS" -ge 1 ]] || fail "expected citations"
curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -d '{"rating":"up"}' "$API/v1/messages/$MSG/feedback" >/dev/null
curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -d '{"rating":"down","comment":"test"}' "$API/v1/messages/$MSG/feedback" >/dev/null
noans=$(curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -d '{"query":"What is the CEO favorite pizza topping on Mars?","stream":false}' "$API/v1/chat")
echo "$noans" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['assistant_message'].get('no_answer') or 'don' in d['assistant_message']['content'].lower()"
pass "ask/citations/feedback/no-answer"

step "25-28 visibility + ACL + delete"
PRIV=$(curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -X PATCH -d '{"visibility":"private","selected_user_ids":[]}' \
  "$API/v1/documents/$DOC_TXT" | json "d['visibility']")
[[ "$PRIV" == "private" ]] || fail "visibility not private"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_B" "$API/v1/documents/$DOC_TXT")
[[ "$code" == "404" ]] || fail "member should not access private doc (got $code)"
msearch=$(curl -sf -b "$COOKIE_B" -H 'Content-Type: application/json' \
  -d '{"query":"18 days paid leave","limit":10}' "$API/v1/search")
echo "$msearch" | python3 -c "import json,sys; d=json.load(sys.stdin); ids=[str(i.get('document_id')) for i in d.get('items',[])]; assert '$DOC_TXT' not in ids"
curl -sf -b "$COOKIE_A" -X DELETE "$API/v1/documents/$DOC_PDF" >/dev/null
pass "visibility ACL + delete"

step "29-36 Google Drive mock connect/sync/search/ask/disconnect"
hdrs="$(mktemp)"
curl -s -D "$hdrs" -o /dev/null -b "$COOKIE_A" -c "$COOKIE_A" "$API/v1/connections/google_drive/oauth/start"
loc=$(tr -d '\r' <"$hdrs" | awk 'tolower($1)=="location:"{print $2; exit}')
rm -f "$hdrs"
code=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_A" -c "$COOKIE_A" "$loc")
[[ "$code" == "302" || "$code" == "303" || "$code" == "200" ]] || fail "drive oauth callback $code"
curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' -X PUT \
  -d '{"folder_ids":["folder-hr"]}' "$API/v1/connections/google_drive/folders" >/dev/null
sync=$(curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -d '{"visibility":"org","incremental":false}' "$API/v1/connections/google_drive/sync")
JOB=$(echo "$sync" | json "d['id']")
for i in $(seq 1 40); do
  job=$(curl -sf -b "$COOKIE_A" "$API/v1/jobs/$JOB")
  st=$(echo "$job" | json "d['status']")
  done_n=$(echo "$job" | json "d.get('progress_done',0)")
  total=$(echo "$job" | json "d.get('progress_total',0)")
  echo "      drive sync $i $st $done_n/$total"
  [[ "$st" == "succeeded" ]] && break
  [[ "$st" == "failed" || "$st" == "dead" ]] && fail "drive sync $st"
  sleep 2
done
for i in $(seq 1 40); do
  n=$(curl -sf -b "$COOKIE_A" "$API/v1/documents?limit=50" | python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(1 for x in d['items'] if x.get('source')=='google_drive' and x.get('status')=='ready'))")
  [[ "$n" -ge 1 ]] && break
  sleep 2
done
dsearch=$(curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -d '{"query":"paid leave per calendar year","limit":5}' "$API/v1/search")
echo "$dsearch" | grep -qi "leave\|18" || fail "drive search empty"
dchat=$(curl -sf -b "$COOKIE_A" -H 'Content-Type: application/json' \
  -d '{"query":"How many paid leave days in HR policy?","stream":false}' "$API/v1/chat")
echo "$dchat" | grep -qi "18" || fail "drive chat not grounded"
curl -sf -b "$COOKIE_A" -X DELETE "$API/v1/connections/google_drive" >/dev/null
after=$(curl -sf -b "$COOKIE_A" "$API/v1/connections/google_drive")
echo "$after" | grep -q '"connected":false' || fail "drive still connected"
pass "drive journey"

step "ops metrics + orphans"
curl -sf -b "$COOKIE_A" "$API/v1/metrics" | grep -q 'documents_by_status'
curl -sf -b "$COOKIE_A" "$API/v1/ops/orphans" | grep -q 'ready_docs_without_chunks'
pass "metrics/orphans"

echo ""
echo "PHASE 1 E2E PASSED"
