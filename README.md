# Vridhi

AI Business Knowledge Assistant for Indian SMB / mid-market companies.

## Phase E — Gmail

OAuth connect/disconnect, encrypted tokens, recursive MIME walk, and attachment ingestion. Reuses the Phase-D schema — Gmail is new `connections` / `documents` rows, not new tables.

- **Connect:** `GET /v1/connections/gmail/oauth/start` (+ callback)
- **Sync:** parent `gmail_sync` job + child `ingest` jobs; progress counts attachments, not messages
- **ACL:** every Gmail document is `private` — a mailbox is personal, so there is no permission graph to resolve. Note `private` does not hide documents from org admins.
- **Scope:** attachments only (email bodies are not indexed); one mailbox per organization, connected by an admin
- **Local:** `GMAIL_MODE=mock` (no Google credentials required)

```bash
docker compose up --build
API_URL=http://localhost:8000 ./scripts/smoke-phase-e.sh
```

### Real Gmail setup (manual, cannot be automated)

Code alone is not enough — the Google Cloud Console project must be configured by hand, and the failure mode is silent:

1. Enable the **Gmail API** on the project.
2. Add `https://www.googleapis.com/auth/gmail.readonly` to the OAuth consent screen's **Data Access** page. Declaring the scope in the authorization URL is *not* sufficient — if it is missing here the user still sees and approves a consent screen, but every API call then returns **403 with no useful error**.
3. `gmail.readonly` is a **restricted** scope: in Testing mode add each account under **Audience → Test users**; production requires Google verification.
4. Add `GOOGLE_GMAIL_REDIRECT_URI` to the OAuth client's **Authorized redirect URIs**.

An existing Drive grant for the same account does **not** cover the Gmail scope — expect a fresh consent screen and a new refresh token.

Then set `GMAIL_MODE=oauth` plus `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` (shared with Drive).

## Phase D — Google Drive

OAuth connect/disconnect, encrypted tokens (Fernet now; Secrets Manager later), folder discovery, incremental Sync Now, real job progress counts, and fail-closed Drive → ACL mapping.

- **Connect:** `GET /v1/connections/google_drive/oauth/start` (+ callback)
- **Folders:** list / save selected folders
- **Sync:** parent `drive_sync` job + child `ingest` jobs; progress from real file counts
- **ACL:** domain/anyone → org (when default allows); matched member emails → selected; else private
- **UI:** Connections — health, last sync, Sync Now, failed docs, sync history
- **Local:** `GOOGLE_DRIVE_MODE=mock` (no Google credentials required)

```bash
docker compose up --build
API_URL=http://localhost:8000 ./scripts/smoke-phase-d.sh
```

UI: http://localhost:8443 → **Connections**

## Phase C — Search + Ask RAG

Hybrid retrieval and grounded answers over indexed company knowledge.

- **Hybrid search:** BM25 + kNN vector in OpenSearch, fused with RRF, then lexical rerank
- **ACL always on:** tenant + visibility (`private` / `org` / `selected`) filters on every query
- **Search:** `POST /v1/search` with passage preview
- **Ask:** `POST /v1/chat` (SSE stream) → retrieve → rerank → grounded LLM / extractive → citations
- **No-answer path:** explicit “I don’t know…” when evidence is weak
- **Citation drawer:** real passage, page/chunk, Open original when `source_url` exists
- **Feedback:** 👍 / 👎 stored per assistant message
- **Golden eval:** `evals/golden.jsonl` (100–300 Qs) + `scripts/run-golden-eval.sh`

All knobs are env-configurable (`RETRIEVAL_*`, `RERANK_TOP_K`, `LLM_PROVIDER=extractive|openai`, etc.).

```bash
docker compose up --build
API_URL=http://localhost:8000 ./scripts/smoke-phase-c.sh
```

UI: http://localhost:8443 → **Ask Vridhi** / **Search**

## Phase B — File upload

Upload → parse → chunk → embed → index (before Drive). See `.env.example` for storage/queue/search backends.

## Quick start

```bash
cp .env.example .env
docker compose up --build
cd frontend && npm install && npm run dev   # :8443
```

API docs: http://localhost:8000/docs

## Key APIs

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/documents/upload` | Upload + ingest |
| GET | `/v1/connections/google_drive/oauth/start` | Drive OAuth connect |
| POST | `/v1/connections/google_drive/sync` | Sync Now (drive_sync job) |
| GET | `/v1/connections/gmail/oauth/start` | Gmail OAuth connect |
| POST | `/v1/connections/gmail/sync` | Sync Now (gmail_sync job) |
| POST | `/v1/search` | Hybrid search + preview |
| POST | `/v1/chat` | Ask (SSE or JSON) |
| GET | `/v1/conversations` | List chats |
| GET | `/v1/conversations/{id}` | Chat + citations |
| POST | `/v1/messages/{id}/feedback` | 👍/👎 |

## Smoke tests

```bash
./scripts/smoke-phase-a.sh
API_URL=http://localhost:8000 ./scripts/smoke-phase-b.sh
API_URL=http://localhost:8000 ./scripts/smoke-phase-c.sh
API_URL=http://localhost:8000 ./scripts/smoke-phase-d.sh
API_URL=http://localhost:8000 ./scripts/smoke-phase-e.sh
```
