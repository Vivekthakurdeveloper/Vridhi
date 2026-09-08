#!/usr/bin/env bash
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR" "$DOCFILE"' EXIT

email="phasec-$(date +%s)@example.com"
password="Password123!"

echo "== register =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase C\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase C Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== features =="
feats=$(curl -sf "$API/v1/features")
echo "$feats" | grep -q '"search_enabled":true' || { echo "FAIL: search_enabled false"; echo "$feats"; exit 1; }
echo "$feats" | grep -q '"ai_query_enabled":true' || { echo "FAIL: ai_query_enabled false"; echo "$feats"; exit 1; }

DOCFILE="$(mktemp).txt"
cat > "$DOCFILE" <<'EOF'
Aranya Foods Q2 business review.
Q2 revenue grew 12 percent year over year for Aranya Foods.
Enterprise customers include Taj Hotels, FreshBasket, and MetroGrocers.
Professional plan is priced at INR 24,999 per month billed annually.
Standard support SLA is 8 business hours for P2 incidents.
EOF

echo "== upload seed doc =="
upload=$(curl -sf -b "$COOKIE_JAR" -F "file=@$DOCFILE;type=text/plain" -F "visibility=org" "$API/v1/documents/upload")
job_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['job']['id'])" <<<"$upload")
for i in $(seq 1 40); do
  status=$(curl -sf -b "$COOKIE_JAR" "$API/v1/jobs/$job_id" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
  echo "  ingest $i $status"
  [[ "$status" == "succeeded" ]] && break
  [[ "$status" == "dead" || "$status" == "failed" ]] && exit 1
  sleep 2
done

echo "== search =="
search=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"query":"Q2 revenue growth","limit":5}' "$API/v1/search")
echo "$search" | grep -qi "revenue" || { echo "FAIL: search miss"; echo "$search"; exit 1; }

echo "== chat (non-stream) =="
chat=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"query":"What was Q2 revenue growth for Aranya Foods?","stream":false}' "$API/v1/chat")
echo "$chat" | grep -qi "12" || { echo "FAIL: chat miss"; echo "$chat"; exit 1; }
msg_id=$(python3 -c "import json,sys; print(json.load(sys.stdin)['assistant_message']['id'])" <<<"$chat")
echo "$chat" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['assistant_message']['citations'], 'no citations'"

echo "== feedback =="
curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"rating":"up"}' "$API/v1/messages/$msg_id/feedback" >/dev/null

echo "== no-answer path =="
na=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d '{"query":"What is the unpublished acquisition price for Company Zebra?","stream":false}' "$API/v1/chat")
echo "$na" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['assistant_message']['no_answer'] or 'know' in d['assistant_message']['content'].lower()"

echo "PHASE C SMOKE PASSED"
