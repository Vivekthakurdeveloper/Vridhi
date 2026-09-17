# Enterprise Google Workspace Auth Foundation — Design Spec

## Context

This is sub-project 1 of 6 in the decomposition of
`docs/Highwatch_Google_Workspace_Enterprise_Connector_Phase_1.md`
("Google Workspace Enterprise Connector — Phase 1"). That requirements
document is too large for a single spec — it spans a new auth model, a
new permission model, a new connector (Chat), extended connectors
(Shared Drives, native Google file types), a sync/deletion overhaul,
and security hardening. This spec covers **only** the first, most
foundational piece: giving Highwatch the ability to securely act as
any employee in a customer's Google Workspace domain.

Everything else in the Phase 1 doc depends on this piece existing, but
this piece does not depend on any of them.

## Goal

Let an admin authorize Highwatch, once, to impersonate any employee in
their Google Workspace domain (via a Google service account and
Domain-Wide Delegation), and give the rest of the app a single,
reliable way to ask: *"give me a working Google API token to act as
this employee."*

## Decisions made (from brainstorming)

- **Every employee individually.** Google Workspace data (Gmail, Drive)
  is inherently per-mailbox/per-user — there is no "org-wide" read
  API. Domain-Wide Delegation works by impersonating one specific user
  at a time.
- **Coexists with, does not replace, today's OAuth connect flow.**
  Drive and Gmail's existing per-admin "Connect" buttons
  (`services/drive.py`, `services/gmail.py`, `services/google_oauth.py`)
  are untouched by this work.
- **Per-customer service account**, not one Highwatch-wide shared
  account. Chosen for quota isolation (a shared project would let one
  customer's sync volume degrade every other customer's — directly
  informed by the Gmail rate-limit issue found this session), blast-
  radius containment (one compromised key affects one customer, not
  all), and because it matches both the requirements doc's own wording
  and the doc's explicit "tenant isolation" security requirement.
- **Narrow scope.** This sub-project is the credential/authorization
  layer only. It proves Highwatch *can* act as any employee. It does
  **not** include looping through all employees to actually sync their
  mail/Drive — that is a separate, later sub-project that will *consume*
  this one.

## Out of scope (explicitly, for this spec)

- Employee enumeration + the actual per-user sync loop (a later
  "sync engine" sub-project)
- The Google Chat connector (net new, doesn't exist today)
- Shared Drives support, native Google Docs/Sheets/Slides handling,
  ZIP extraction
- Google Groups-based permission resolution
- Continuous/incremental sync and deletion propagation across sources
- The unified "Connect Google Workspace" onboarding UI (depends on
  this plus later sub-projects existing first)

## Architecture

New, fully separate module — does not modify any existing Drive/Gmail
code:

- **`apps/api/app/services/workspace_enterprise.py`** — the only
  module that knows how to impersonate a user. Core function: given a
  tenant and an employee's email, return a working Google API access
  token for that employee. Uses Google's own `google-auth` library
  (not the hand-rolled `google_oauth.py`, which does human-consent
  flows and does not apply to server-to-server delegation):

  ```python
  creds = service_account.Credentials.from_service_account_info(
      key_dict, scopes=[...]
  ).with_subject(employee_email)
  ```

- **`apps/api/app/routers/workspace_enterprise.py`** — the four admin-
  facing endpoints (below).

Later sub-projects (Chat connector, sync engine) will import and call
`workspace_enterprise.py` to obtain tokens. That integration is their
responsibility, not this spec's.

## Data model

One new table, one new Alembic migration (next phase number after
Gmail's `0005`):

**`workspace_enterprise_connections`** — one row per tenant (org-wide
capability, not per-connector, so unique on `tenant_id` unlike
`Connection`, which is unique on `(tenant_id, connector_type)`):

| Field | Type | Purpose |
|---|---|---|
| `id` | UUID PK | |
| `tenant_id` | UUID FK → `organizations`, **unique** | One per org |
| `google_domain` | text | Customer's Workspace domain (e.g. `acme.com`) |
| `service_account_email` | text | From the key JSON; stored in the clear (not a secret) for display/audit |
| `encrypted_key` | text | Full service-account JSON key, Fernet-encrypted via the existing `TokenStore` — no new crypto |
| `status` | enum | `pending_verification` \| `verified` \| `error` \| `disabled` |
| `verified_scopes` | text | Space-separated scopes confirmed working at last verification |
| `last_verified_at` | timestamp, nullable | |
| `last_error` | text, nullable | Raw error, for support/debugging (see Error Handling) |
| `created_by_user_id` | UUID FK → `users` | Which admin submitted it |
| `created_at` / `updated_at` | timestamp | |

## Setup & verification flow

1. **Instructions.** A setup page tells the admin what to do in their
   own Google Cloud/Admin Console: create a service account, enable
   required APIs, authorize that service account's Client ID for
   Domain-Wide Delegation with the required scopes (starting with
   `admin.directory.user.readonly` — the minimum needed to list
   employees at all; Gmail/Drive/Chat scopes are added as those later
   sub-projects land).
2. **Submit.** Admin uploads/pastes the service-account JSON key plus
   the Workspace domain. Row is created as `pending_verification` —
   **not yet treated as working.**
3. **Verify, immediately and for real.** Rather than trusting that
   submission means it works (the exact mistake that made this
   session's Gmail OAuth debugging painful), Highwatch immediately:
   - Impersonates the submitting admin's own email (guaranteed to
     exist; the admin is present to notice anything wrong)
   - Calls Google's Admin Directory API to list 1 user in the domain

   This single check proves the key is valid, DWD is actually
   authorized (not just clicked through), the domain matches, and it
   doubles as a real test of the exact capability a later sub-project
   needs (employee enumeration) — not a throwaway check.
4. **Result.** Success → `status: verified`, `last_verified_at` set.
   Failure → `status: error` with a *translated* message (see below)
   and a "Retry verification" action that re-tests without requiring
   re-upload of the key.

## API endpoints

All under `/v1/enterprise/google-workspace`, admin-only
(`require_role(admin)`) except where noted:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/enterprise/google-workspace` | Submit/replace the key + domain. Runs verification synchronously as part of the same call (submit and test in one step) |
| `POST` | `/v1/enterprise/google-workspace/verify` | Re-run verification only, no re-upload |
| `GET` | `/v1/enterprise/google-workspace` | Status (domain, service account email, status, verified scopes, last verified/error). Never returns the key. Readable by any authenticated org member, matching how connector status is visible today |
| `DELETE` | `/v1/enterprise/google-workspace` | Disable/remove — same pattern as Drive/Gmail's Disconnect |

## Error handling

Never surface Google's raw error to the admin — translate known
failure signatures into a specific, actionable message. The raw error
is still stored in `last_error` for support/debugging.

| Google's actual signal | What it really means | Message shown to admin |
|---|---|---|
| Malformed/incomplete JSON on submit | Not a full/valid service-account key | "This doesn't look like a valid service account key — make sure you copied the entire JSON file." |
| `unauthorized_client: ... not authorized for any of the scopes requested` | The Admin Console DWD authorization step was skipped or done for the wrong Client ID/scopes — the DWD equivalent of this session's Gmail-scope bug | "Domain-Wide Delegation isn't authorized for this service account yet. In Google Admin Console → Security → API Controls → Domain-wide Delegation, authorize Client ID `<shown>` for scope `admin.directory.user.readonly`." |
| `invalid_grant: Invalid email or User ID` | Impersonated email isn't a real, active domain user | "Couldn't act as `<email>` — check this is an active user in your Google Workspace." |
| 403 on the directory check | Google-specific quirk: reading directory data via DWD requires impersonating a **Super Admin**, regardless of granted scopes | "This account doesn't have Super Admin privileges. Directory access requires impersonating a Super Admin — use a different admin account for verification." |
| Network/DNS-type errors | Transient, not a config problem (we hit exactly this kind of blip this session) | Retried once automatically before ever being shown as an error |

**Hard rule:** the service-account key and any minted access tokens
are never written to logs — only non-secret fields (domain, service
account email, status) appear in log lines. Satisfies the requirements
doc's explicit "must never expose Google credentials or access tokens
in application logs."

## Security & storage

- **Encryption:** same Fernet-based `TokenStore` Drive/Gmail already
  use — no new crypto mechanism.
- **Access control:** submit/verify/disable are admin-only; status is
  readable org-wide (no secrets in it).
- **Audit logging:** every submit/verify/disable writes an
  `AuditEvent` (who, when, tenant), reusing the existing pattern —
  satisfies the doc's "Audit logs" requirement.
- **Tenant isolation:** `tenant_id` unique + scoped on every query,
  same as the rest of the app.
- **Key rotation:** not a separate feature. Re-submitting via `POST`
  overwrites the old key and re-verifies (upsert), same shape as
  Gmail's `_upsert_connection`.

## Testing strategy

Cannot be tested against a real Workspace domain in local dev. Follows
the existing `GMAIL_MODE=mock` / `GOOGLE_DRIVE_MODE=mock` pattern:

- **`WORKSPACE_ENTERPRISE_MODE=mock`** — verification succeeds against
  a fixture domain/fake directory listing, no real Google calls. Lets
  an automated smoke test (`smoke-phase-f.sh`, following the existing
  lettering convention) exercise submit → verify → status → disable
  end-to-end.
- **Real-mode testing** genuinely requires an actual Google Workspace
  domain with Super Admin access — not available in this environment.
  Flagging this now, honestly, the same way this session's Gmail work
  showed that mock mode alone can't catch everything real accounts
  surface. Needs to be scheduled once a real test Workspace domain is
  available.

## Open follow-ups

- Real-mode verification needs a real Google Workspace test domain
  with Super Admin access — not yet available.
- The specific list of scopes needed grows as later sub-projects
  (Gmail-via-DWD, Drive-via-DWD, Chat) land; this spec only requires
  `admin.directory.user.readonly` to prove the foundation works.
