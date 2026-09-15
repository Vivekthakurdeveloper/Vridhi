#!/usr/bin/env bash
# Phase F smoke: submit a fixture service-account key (mock mode) -> verify -> status -> disable
set -euo pipefail

API="${API_URL:-http://localhost:8000}"
COOKIE_JAR="$(mktemp)"
trap 'rm -f "$COOKIE_JAR"' EXIT

email="phasef-$(date +%s)@example.com"
password="Password123!"

echo "== register =="
curl -sf -c "$COOKIE_JAR" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Phase F\",\"email\":\"$email\",\"password\":\"$password\",\"organization_name\":\"Phase F Co\"}" \
  "$API/v1/auth/register" >/dev/null

echo "== status before submit =="
before=$(curl -sf -b "$COOKIE_JAR" "$API/v1/enterprise/google-workspace")
echo "$before" | grep -q '"connected":false' || {
  echo "FAIL: expected not connected before submit"
  echo "$before"
  exit 1
}

echo "== submit (mock) =="
# Built entirely in Python (not interpolated through bash) so the PEM
# newlines round-trip correctly: json.dumps(fixture_key) here escapes the
# real \n characters into the two-char "\n" sequence JSON requires, and
# the outer json.dumps then correctly re-escapes that already-JSON string
# as the value of service_account_key. Interpolating a bash variable
# containing literal \n sequences through a Python triple-quoted string
# would instead turn them into raw newline bytes inside the JSON payload,
# which json.loads() on the API side would reject as an invalid control
# character.
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
submit=$(curl -sf -b "$COOKIE_JAR" -H 'Content-Type: application/json' -d "$body" \
  "$API/v1/enterprise/google-workspace")
echo "$submit" | grep -q '"status":"verified"' || {
  echo "FAIL: expected verified status after mock-mode submit"
  echo "$submit"
  exit 1
}
echo "$submit" | grep -q '"service_account_email":"highwatch-sync@acme-proj.iam.gserviceaccount.com"' || {
  echo "FAIL: service_account_email not stored/returned correctly"
  echo "$submit"
  exit 1
}

echo "== re-verify =="
curl -sf -b "$COOKIE_JAR" -X POST "$API/v1/enterprise/google-workspace/verify" \
  | grep -q '"status":"verified"' || {
  echo "FAIL: re-verify did not return verified"
  exit 1
}

echo "== disable =="
curl -sf -b "$COOKIE_JAR" -X DELETE "$API/v1/enterprise/google-workspace" >/dev/null
after=$(curl -sf -b "$COOKIE_JAR" "$API/v1/enterprise/google-workspace")
echo "$after" | grep -q '"status":"disabled"' || {
  echo "FAIL: expected disabled after DELETE"
  echo "$after"
  exit 1
}

echo "PHASE F SMOKE PASSED"
