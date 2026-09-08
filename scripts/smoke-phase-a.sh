#!/usr/bin/env bash
# Phase A smoke: signup → org → invite → logout → login → accept invite → role change → deactivate
set -euo pipefail

API="${API_URL:-http://localhost:8001}"
COOKIE_A="$(mktemp)"
COOKIE_B="$(mktemp)"
STAMP="$(date +%s)"
OWNER_EMAIL="owner-${STAMP}@example.com"
MEMBER_EMAIL="member-${STAMP}@example.com"
PASSWORD="Secret123!"

cleanup() { rm -f "$COOKIE_A" "$COOKIE_B"; }
trap cleanup EXIT

json_field() {
  python3 - "$1" "$2" <<'PY'
import json,sys
data=json.load(open(sys.argv[1]))
path=sys.argv[2].split(".")
cur=data
for p in path:
    if isinstance(cur, list):
        cur=cur[int(p)]
    else:
        cur=cur[p]
print(cur if cur is not None else "")
PY
}

echo "==> Health"
curl -sf "$API/healthz" >/dev/null

echo "==> Register owner + org"
curl -sf -c "$COOKIE_A" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Owner ${STAMP}\",\"email\":\"${OWNER_EMAIL}\",\"password\":\"${PASSWORD}\",\"organization_name\":\"Acme ${STAMP}\"}" \
  "$API/v1/auth/register" > /tmp/vridhi_reg.json
ORG_ID="$(json_field /tmp/vridhi_reg.json membership.tenant_id)"
OWNER_ID="$(json_field /tmp/vridhi_reg.json user.id)"
echo "    owner=$OWNER_EMAIL org=$ORG_ID"

echo "==> Invite member"
curl -sf -b "$COOKIE_A" -c "$COOKIE_A" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${MEMBER_EMAIL}\",\"role\":\"member\"}" \
  "$API/v1/users/invite" > /tmp/vridhi_invite.json
INVITE_TOKEN="$(json_field /tmp/vridhi_invite.json debug_token)"
INVITE_ID="$(json_field /tmp/vridhi_invite.json id)"
if [[ -z "$INVITE_TOKEN" ]]; then
  echo "ERROR: debug_token missing. Run API with APP_ENV=development and EMAIL_PROVIDER=log"
  exit 1
fi
echo "    invite=$INVITE_ID"

echo "==> List pending invites"
curl -sf -b "$COOKIE_A" "$API/v1/users/invites" > /tmp/vridhi_invites.json
python3 - <<PY
import json
rows=json.load(open("/tmp/vridhi_invites.json"))
assert any(r["id"]=="$INVITE_ID" for r in rows), rows
print("    pending invites ok")
PY

echo "==> Logout owner"
curl -sf -b "$COOKIE_A" -c "$COOKIE_A" -X POST "$API/v1/auth/logout" >/dev/null

echo "==> Accept invite as new member"
curl -sf -c "$COOKIE_B" -H 'Content-Type: application/json' \
  -d "{\"token\":\"${INVITE_TOKEN}\",\"name\":\"Member ${STAMP}\",\"password\":\"${PASSWORD}\"}" \
  "$API/v1/users/invite/accept" > /tmp/vridhi_accept.json
MEMBER_ID="$(json_field /tmp/vridhi_accept.json user.id)"
echo "    member=$MEMBER_EMAIL"

echo "==> Member logout + login"
curl -sf -b "$COOKIE_B" -c "$COOKIE_B" -X POST "$API/v1/auth/logout" >/dev/null
curl -sf -c "$COOKIE_B" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${MEMBER_EMAIL}\",\"password\":\"${PASSWORD}\"}" \
  "$API/v1/auth/login" > /tmp/vridhi_member_login.json
python3 - <<PY
import json
s=json.load(open("/tmp/vridhi_member_login.json"))
assert s["membership"]["role"]=="member"
print("    member login ok")
PY

echo "==> Owner login + promote member to admin"
curl -sf -c "$COOKIE_A" -H 'Content-Type: application/json' \
  -d "{\"email\":\"${OWNER_EMAIL}\",\"password\":\"${PASSWORD}\"}" \
  "$API/v1/auth/login" >/dev/null
curl -sf -b "$COOKIE_A" -X PATCH -H 'Content-Type: application/json' \
  -d '{"role":"admin"}' \
  "$API/v1/users/${MEMBER_ID}" > /tmp/vridhi_patch.json
python3 - <<PY
import json
m=json.load(open("/tmp/vridhi_patch.json"))
assert m["role"]=="admin", m
print("    role change ok")
PY

echo "==> Deactivate member"
curl -sf -b "$COOKIE_A" -X DELETE "$API/v1/users/${MEMBER_ID}" >/dev/null

echo "==> Features endpoint"
curl -sf "$API/v1/features" > /tmp/vridhi_features.json
python3 - <<PY
import json
f=json.load(open("/tmp/vridhi_features.json"))
assert "google_login_enabled" in f
print("    features ok google_login_enabled=", f["google_login_enabled"])
PY

echo
echo "Phase A smoke PASSED"
echo "  owner:  $OWNER_EMAIL"
echo "  member: $MEMBER_EMAIL"
echo "  org:    $ORG_ID"
