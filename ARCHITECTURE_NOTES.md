# Vridhi.ai — Architecture Notes

A code-derived walkthrough of the repo as of commit `89f1377` (branch `main`). Everything below was traced through the actual source, not the README. Where the README and the code disagree, the code wins and I say so.

**TL;DR:** Vridhi is a multi-tenant RAG app: FastAPI + Postgres + OpenSearch + S3 + SQS, with a Python worker doing all ingestion. Phases A–D (auth, upload, RAG, Drive) are structurally complete and wired end-to-end. But the default local configuration runs on *stand-ins for every AI-shaped component*: `EMBEDDING_PROVIDER=hash` (not real embeddings), `LLM_PROVIDER=extractive` (not an LLM), `GOOGLE_DRIVE_MODE=mock` (three hardcoded fixture files), `EMAIL_PROVIDER=log`. The plumbing is real; the intelligence is a placeholder. See §6.

---

## 1. Big picture — the end-to-end user journey

### 1.1 Signup → organization

Frontend routes live in `frontend/src/App.tsx`. An unauthenticated visitor hits `LandingRedirect` → `/login`.

**Register** (`POST /v1/auth/register`, `apps/api/app/routers/__init__.py`) calls `AuthService.register` (`apps/api/app/services/auth.py:174`), which in one transaction:

1. Creates a `User` (bcrypt password hash via `passlib`).
2. If `organization_name` was supplied, creates an `Organization` **and** an `OrganizationMember` with `role=owner`. The org's `id` *is* the `tenant_id` — this is stated explicitly at `apps/api/app/models/__init__.py:72`.
3. Issues an email-verification token and "sends" it (log-only by default — §6).
4. Creates a `Session` row storing **only a SHA-256 hash** of the session token (`hash_token`, `app/security/__init__.py`), and returns the raw token to be set as an `HttpOnly` cookie (`_set_session_cookie`).
5. Writes an `AuditEvent`.

Note the session row carries `tenant_id` (`models/__init__.py:155`). **The active tenant is pinned to the session at login time**, chosen as the user's oldest active membership (`auth.py:236`). There is no tenant-switch endpoint anywhere in the codebase — a user in two orgs can only ever act in their first one.

If the user registered without an org name, `RequireOrg` in `App.tsx` bounces them to `/onboarding`, which calls `POST /v1/organizations` → `OrgService.create_organization`. That method *hard-refuses* a second org: `"You already belong to an organization in Phase 1."` (`auth.py`, `ORG_ALREADY_EXISTS`).

**Invites** are the other entry path: admin+ calls `POST /v1/users/invite` → emailed token → `POST /v1/users/invite/accept` creates the user, the membership, and a session in one shot. In development with `EMAIL_PROVIDER=log`, the raw invite token is echoed back in the API response as `debug_token` (`routers/__init__.py`, `_invite_out`) — this is what the smoke scripts use, and it is correctly gated to `app_env == "development"` *and* `email_provider == "log"`.

### 1.2 Connect a source

Two connectors are described in detail here:

- **File upload** — `POST /v1/documents/upload` (multipart). Lazily creates a `Connection(connector_type="file_upload")` per tenant (`documents.py:78`).
- **Google Drive** — OAuth connect, folder pick, automatic sync every 15 minutes (or "Sync Now"). Full trace in §5.

Gmail is a third real connector (OAuth, attachment sync, History-based deletion; see §5.2 and §8), and it also appears in the catalog (`routers/workspace.py`, `_connector_catalog`).

The UI reads `GET /v1/connectors` and `GET /v1/features` to decide what to render; `features` is computed from config readiness properties (`config.py`: `file_upload_ready`, `search_ready`, `ai_query_ready`, `google_drive_ready`), so a misconfigured backend degrades to a disabled button rather than a runtime error. That's a genuinely nice piece of design.

### 1.3 Upload → answer

```
POST /v1/documents/upload
  └─ DocumentService.upload (services/documents.py:172)
       ├─ validate ext + MIME + size
       ├─ storage.put_bytes(tenants/<tenant>/<doc>/<ver>/<file>)   ← S3 / filesystem
       ├─ INSERT documents, document_versions, document_grants
       ├─ INSERT sync_jobs (job_type=ingest, status=queued)
       ├─ queue.enqueue_ingest(...)                                 ← SQS message or DB no-op
       └─ COMMIT, return 200 with job id
                                  ⋮ (async boundary)
worker consumer.poll_*_once (worker/consumer.py:86 / :112)
  └─ process_ingest_job (worker/ingest.py:30)
       ├─ storage.get_bytes → parse_bytes (pypdf / python-docx / openpyxl / python-pptx / decode)
       ├─ chunk_text(800 chars, 120 overlap)
       ├─ delete old chunks + OpenSearch docs for this document
       ├─ INSERT chunks
       ├─ _embed_batch → hash_embed or openai_embed (384-dim)
       ├─ search.index_chunk(...) per chunk  ← includes ACL fields + vector
       ├─ INSERT embeddings (metadata only; vectors live in OpenSearch)
       └─ documents.status = ready, sync_jobs.status = succeeded
```

The UI polls `GET /v1/jobs/{id}` (and `GET /v1/documents/{id}/job`) until `succeeded`.

Then **Ask** (`POST /v1/chat`) or **Search** (`POST /v1/search`) retrieve over that index with ACL filters applied inside the OpenSearch query. Full trace in §4.

---

## 2. Architecture — who talks to whom

```
┌──────────────┐  cookie-auth fetch/SSE   ┌──────────────┐
│  frontend    │ ───────────────────────► │   API        │
│  Vite :8443  │ ◄─────────────────────── │ FastAPI :8000│
└──────────────┘                          └──────┬───────┘
                                                 │ sync
              ┌──────────────┬───────────────────┼──────────────────┐
              ▼              ▼                   ▼                  ▼
        ┌──────────┐  ┌────────────┐      ┌───────────┐     ┌────────────┐
        │ Postgres │  │ S3/LocalSt.│      │OpenSearch │     │ SQS        │
        │ (truth)  │  │ (bytes)    │      │(chunks+vec)│    │ (job bus)  │
        └────┬─────┘  └─────┬──────┘      └─────┬─────┘     └─────┬──────┘
             │              │                    │                │
             └──────────────┴────────┬───────────┴────────────────┘
                                     ▼
                              ┌─────────────┐
                              │   worker    │ python -m worker
                              │ (poll loop) │
                              └─────────────┘
```

### Synchronous (request/response)

Everything under `/v1/*` is a normal blocking FastAPI handler with a per-request SQLAlchemy session (`app/db.py:get_db`). Notably **search and chat are fully synchronous**, including the OpenSearch round-trips and, when `LLM_PROVIDER=openai`, a blocking `httpx.post` with a 90-second timeout inside an async route (`generation.py:_generate_openai`). That will block the event loop under load — see §8.

`POST /v1/chat` *looks* streaming but isn't. `RagService.ask` completes retrieval, generation, DB writes and commit **before** the SSE generator starts, then `iter_stream_tokens` slices the finished string into 24-character pieces and emits them as `token` events (`routers/intelligence.py`; `generation.py:61`). It's cosmetic streaming — time-to-first-token equals total latency.

### Asynchronous (queue → worker)

Ingestion, Drive sync and Gmail sync are async (and the worker loop also runs the automatic-sync scheduler, §5.2). Two interchangeable queue backends (`services/queue.py`):

| `QUEUE_BACKEND` | API behaviour | Worker behaviour |
|---|---|---|
| `sqs` | `send_message` to `vridhi-ingest`; DLQ after 5 receives (`infra/localstack/init-aws.sh`) | long-poll `receive_message`, delete on success |
| `db` | no-op; the `sync_jobs` row *is* the queue | `SELECT ... WHERE status='queued' ORDER BY created_at` every 2s |

The `db` backend is the local-dev escape hatch (no LocalStack needed). It has real limitations — no row locking, and failed jobs are never retried, since the poll only ever selects `queued`. See §8.

Job types are dispatched in `worker/consumer.py:_dispatch_job`: `drive_sync` → `process_drive_sync_job`, `gmail_sync` → `process_gmail_sync_job`, everything else → `process_ingest_job`.

**The worker imports the API package.** `apps/worker/Dockerfile` copies `apps/api` to `/api` and sets `PYTHONPATH=/api:/app`; `worker/drive_sync.py` imports `app.services.drive`, `app.services.queue`, `app.services.storage`, `app.services.tokens` directly. So they are separate *processes* but not separate *codebases* — a change to `app.services.drive` changes worker behaviour. Worth knowing before you refactor anything under `app/services/`.

### Storage responsibilities

| Store | Holds | Source of truth for |
|---|---|---|
| **Postgres** | orgs, users, memberships, sessions, invites, audit, connections, encrypted OAuth tokens, documents, versions, grants, **chunk text**, embedding metadata, sync jobs, conversations, messages, citations, feedback | Everything transactional; ACL decisions; job state |
| **S3 / filesystem** | original file bytes at `tenants/<tenant_id>/<doc_id>/<version_id>/<filename>` (`services/storage.py`) | Immutable raw artifacts, re-ingestion input |
| **OpenSearch** | one doc per chunk: `content`, `title`, denormalised ACL fields (`tenant_id`, `visibility`, `uploaded_by_user_id`, `granted_user_ids`, `granted_group_ids`), and a 384-dim `knn_vector` | Retrieval only — it's a *derived* index, rebuildable from Postgres + S3 |

Chunk text is stored **twice** (Postgres `chunks.content` and OpenSearch `content`). Postgres backs document preview; OpenSearch backs retrieval. Note that citation passages returned by `/v1/chat` come from the *OpenSearch* copy, not the Postgres one (`rag.py:ask` persists `chunk.content[:2000]` from the retrieved hit).

---

## 3. Data model & tenant isolation

Everything lives in one file: `apps/api/app/models/__init__.py` (660 lines), with four Alembic migrations mirroring the four phases (`0001_phase1_foundation` → `0004_phase_d_drive`).

### Core entities

**`Organization`** (`:71`) — the tenant. `id` is `tenant_id` everywhere else. Has `name`, unique `slug`.

**`User`** (`:90`) — **global, not tenant-scoped**. Unique on `email` across the whole system. `password_hash` is nullable (Google-only accounts). This is the one entity deliberately outside tenant scope.

**`OrganizationMember`** (`:114`) — the join table and the real RBAC carrier. Unique on `(tenant_id, user_id)`. Roles are ranked `member=1 < admin=2 < owner=3` (`security/__init__.py:ROLE_RANK`), compared via `role_at_least`.

**`Session`** (`:144`) — `token_hash` (SHA-256, unique) + `expires_at` + `revoked_at`, plus **`tenant_id`**. This is where tenant pinning happens. `AuthService.get_session_by_token` (`auth.py:113`) re-validates on every request that the membership still exists and is `active`, so deactivating a member takes effect immediately without session revocation.

**`Connection`** (`:275`) — one row per `(tenant_id, connector_type)`, unique-constrained. `config` JSONB holds `selected_folder_ids`, `default_visibility`, `page_token`, and `file_cursors` (the Drive incremental state). `ConnectionCredential` (`:313`) is a separate table holding Fernet-encrypted OAuth tokens — the docstring is explicit that tokens must never land in `config`, and the code honours that.

**`Document`** (`:343`) / **`DocumentVersion`** (`:414`) / **`DocumentGrant`** (`:387`) — documents are soft-deleted (`deleted_at` + `status=deleted`). Versions are append-only with `version_number`, `storage_key`, `checksum_sha256`; `Document.current_version_id` points at the live one (but is a bare UUID column, not an FK — deliberate, to avoid a circular FK). `external_id` + a partial unique index `(tenant_id, source, external_id) WHERE external_id IS NOT NULL` is what makes Drive sync idempotent.

**`Chunk`** (`:441`) / **`EmbeddingMeta`** (`:468`) — chunk text and token counts in Postgres; `EmbeddingMeta` records only `model`, `dimensions`, and `opensearch_id` (`"<tenant_id>:<chunk_id>"`). Comment at `:469` says it plainly: *"vectors live in OpenSearch."*

**`SyncJob`** (`:499`) — one table for two job types (`ingest`, `drive_sync`) with `attempt`/`max_attempts`, four progress counters, and a `payload` JSONB. A `drive_sync` job is the parent; it spawns N `ingest` children (`drive_sync.py:_upsert_drive_file`), but the only link between them is `connection_id` — there is no `parent_job_id`, so the parent reports `succeeded` once files are *enqueued*, not once they're *indexed*. The Phase D smoke script compensates by polling documents afterwards.

**`Conversation` / `Message` / `MessageCitation` / `AnswerFeedback`** (`:546`–`:660`) — `Message.no_answer` is a first-class boolean. Citations are **denormalised snapshots** (`title`, `passage`, `page`, `score`, `source_url` copied in at answer time) with nullable FKs back to document/chunk, so a citation survives document deletion. Feedback is unique per `(message_id, user_id)` and upserted.

### Where tenant isolation is actually enforced

There is **no** Postgres row-level security and no global query filter. Isolation is enforced by hand at four layers:

1. **Session → context.** `deps.py:load_session` resolves the cookie into a `RequestContext`; `require_tenant` 400s if there's no membership; `require_role(MemberRole.admin)` guards privileged routes.
2. **Every service query filters on `tenant_id` explicitly.** E.g. `DocumentService.accessible_filter` (`documents.py:149`), `RagService.get_conversation`, `DriveService.get_connection`. I did not find a document/chunk/job/conversation read path that omits the tenant predicate.
3. **Visibility layered on top of tenancy.** `can_access` (`documents.py:119`) and its SQL twin `accessible_filter` (`:149`) implement the same rule set — deleted → deny; admin+ → allow all within tenant; own upload → allow; `org` → allow; `private` → deny; `selected` → allow iff a `DocumentGrant` row exists **or** a `DocumentGroupGrant` row names a Google Group the user belongs to (`group_memberships`). Keeping these two in sync manually is a maintenance hazard (§8).
4. **The same rule re-expressed as an OpenSearch filter.** `OpenSearchRetriever.acl_filter` (`retrieval.py:61`) always adds `{"term": {"tenant_id": ...}}` and, for non-admins, a `should` clause over `uploaded_by_user_id` / `visibility=org` / (`visibility=selected` AND (`granted_user_ids` contains uid **OR** `granted_group_ids` intersects the caller's group ids)) with `minimum_should_match: 1`. The `granted_group_ids` `terms` clause is only added when the caller actually has groups. This is applied as a `filter` on **both** the BM25 and the kNN query — so the vector search cannot leak across tenants either. That's the right call and it's done consistently.

The important structural consequence: **ACL state is denormalised into OpenSearch at ingest time**, so the index is only correct as long as it's re-written whenever visibility or grants change. For Drive-sourced documents that now happens: the sync's content-unchanged skip path re-resolves permissions every run and enqueues a re-ingest for the current version whenever the resolved `(visibility, grants, group grants)` differs from what was stored (`drive_sync.py:_upsert_drive_file` → `_enqueue_ingest_job`; `ingest.py`'s delete-then-reindex is idempotent, so the replay is safe and only costs work when the ACL actually moved). It is still *not* true for ACL changes made from inside Vridhi or for deletes — see §8, items 1 and 2.

---

## 4. The RAG / AI pipeline

### 4.1 Ingest (worker)

`worker/ingest.py:process_ingest_job`:

- **Parse** — `pipeline/process.py:parse_bytes` dispatches on extension then MIME: pypdf, python-docx, openpyxl (`read_only`, `data_only`), python-pptx, or UTF-8 decode with `errors="replace"`. Unknown types raise.
- **Chunk** — `chunk_text` is a plain character-window splitter: `CHUNK_SIZE=800`, `CHUNK_OVERLAP=120`, with a nicety that it backs off to the last `\n\n` / `\n` / space if that break lands past 50% of the window. No token counting, no semantic/structural awareness, and — importantly — **no page or slide tracking**. `estimate_tokens` is just `len/4`.
- **Idempotency** — deletes prior `chunks`/`embeddings` rows for the version *and* calls `search.delete_by_document` before re-indexing (`ingest.py:87`). Re-running a job is safe.
- **Embed** — `_embed_batch` batches at `EMBEDDING_BATCH_SIZE=32`. `hash_embed` is a deterministic signed-hash bag-of-words projection into 384 dims, L2-normalised. `openai_embed` is a straight `/v1/embeddings` call.
- **Index** — one OpenSearch doc per chunk, `id = "<tenant_id>:<chunk_id>"`, `refresh=True` on every single write (fine for demo volumes, a throughput problem later). Index mapping is created lazily by `ensure_index` (`pipeline/index.py`): `knn: true`, HNSW, `cosinesimil`, `nmslib` engine.

Failure handling: on exception the job goes `failed`, or `dead` once `attempt >= max_attempts` (default 5), the document goes `failed` with the error string, and the exception is re-raised so SQS doesn't delete the message.

### 4.2 Retrieval

`OpenSearchRetriever.hybrid_search` (`retrieval.py:92`) runs **two independent queries** — BM25 `multi_match` over `title^2, content`, and a kNN query over `embedding` — each with the ACL filter, each returning 40 hits. It fuses them with **Reciprocal Rank Fusion**: `score = Σ 1/(60 + rank)`, then truncates to `RETRIEVAL_TOP_K=40`.

Then `rerank_chunks` (`retrieval.py:202`) — despite the name, this is **not** a cross-encoder. It's lexical overlap:

```
combined = rrf_score + |query_tokens ∩ chunk_tokens| / |query_tokens| + (0.15 if a query token appears in the title)
```

and keeps the top `RERANK_TOP_K=8`. The docstring is honest about it ("no external model required"). Note the mutation: `chunk.score` is **overwritten** with `combined`, which matters for the next step because the two quantities are on completely different scales (RRF scores are ~0.016–0.03; overlap is 0–1).

### 4.3 Generation and the "I don't know" decision

This is the part worth reading carefully, because the no-answer logic is spread across three places and the gate is scale-sensitive.

**Gate 1 — evidence floor** (`generation.py:39`):

```python
usable = [c for c in chunks if c.score >= min_score and c.content.strip()]
if not usable:  →  no_answer
```

`MIN_CITATION_SCORE=0.08`. Because `rerank_chunks` already replaced `score` with `rrf + overlap + title_bonus`, a chunk with *zero* lexical overlap has score ≈ 0.016–0.033 (pure RRF) and fails the 0.08 floor, while any chunk sharing ~8% of query tokens passes. So this threshold is effectively "did at least one query word appear in the chunk", not a calibrated relevance score. **If you swap in a real reranker, this constant is meaningless and must be re-derived.**

**Gate 2 — extractive refusal** (`generation.py:67`, `_generate_extractive`):

```python
if evidence_hits == 0 and top[0].score < max(min_citation_score * 2, 0.12):  →  no_answer
```

where `evidence_hits` counts how many of the top 4 chunks share a 3+ character token with the query. Note it's an **AND** — a high-scoring chunk with no token overlap still produces an answer.

**Gate 3 — OpenAI echo detection** (`generation.py:_generate_openai`): the system prompt instructs the model to reply with the exact `NO_ANSWER_PHRASE`; the code then does a fuzzy string check (`phrase in answer.lower()` and `len(answer) < len(phrase) + 40`) to reclassify. Brittle — a model that says "I don't know based on the available company knowledge, but you might check the HR folder" (i.e. >40 chars) would be scored as a real answer.

**What the extractive answerer actually produces:** it picks up to 4 sentences (≥40 chars) from the top 4 chunks that share a query token, appends `[n]` markers, and prefixes `"Based on your company knowledge:\n\n"`. If no sentence qualifies it falls back to the first 420 characters of the top chunk with `[1]`. So the default "AI answer" is **sentence extraction with citation markers** — there is no model in the loop unless you set `LLM_PROVIDER=openai` and a key.

**Citations** — on the answer path, `RagService.ask` (`rag.py:127`) persists a `MessageCitation` per returned chunk with rank, title, `passage[:2000]`, page, chunk_index, score, and `source_url` (falling back to a per-document lookup in `_source_urls`). On the no-answer path **zero citations are written**, which is why the UI can render "I don't know" cleanly.

**Search** (`/v1/search`) shares retrieval + rerank but skips generation entirely, returning a `SEARCH_PREVIEW_CHARS=280` snippet per hit.

---

## 5. Google Drive integration

### 5.1 OAuth connect

`GET /v1/connections/google_drive/oauth/start` (admin+ only) generates CSRF state, stores it in an `HttpOnly` `vridhi_drive_oauth_state` cookie (10 min), and 302s to `DriveService.oauth_start_url` (`services/drive.py:196`).

- In **mock mode**, that URL is the app's *own* callback with `?code=mock` — the whole flow stays local.
- In **oauth mode**, it's the real Google consent URL with `access_type=offline`, `prompt=consent`, scope `drive.readonly userinfo.email`.

The callback (`routers/drive.py`) compares the state cookie constant-length-ish (`expected != state`), exchanges the code (`drive.py:212`), and **requires a refresh token** — if Google omits one (a re-consent quirk) it fails with an actionable message rather than silently creating a connection that can't refresh. `complete_oauth` then fetches the account email and calls `_upsert_connection`, which Fernet-encrypts both tokens into `connection_credentials`.

The callback route is guarded by `require_role(admin)`, i.e. it depends on the user's own session cookie surviving the Google round-trip. Both cookies are `SameSite=Lax` and the return is a top-level GET navigation, so this works — but it's a subtle coupling worth remembering if anyone ever changes the cookie policy to `Strict`.

`_access_token` (`drive.py:516`) is the refresh path: returns the cached access token if >30s from expiry, else refreshes and re-encrypts. In mock mode it short-circuits to `"mock-access-token"`.

**Token encryption** (`services/tokens.py`): `FernetTokenStore` accepts either a real Fernet key or any secret, deriving `urlsafe_b64encode(sha256(secret))` otherwise. `SecretsManagerTokenStore` is an explicit `RuntimeError` stub. There is **no key rotation and no key versioning** — `connection_credentials.token_backend` records the backend but not a key id, so rotating `TOKEN_ENCRYPTION_KEY` orphans every stored token.

### 5.2 Sync

`POST /v1/connections/google_drive/sync` → `DriveService.start_sync` (`drive.py:394`) writes folder selection + default visibility into `connection.config`, flips the connection to `syncing`, creates a **parent `drive_sync` job**, and enqueues it. The worker picks it up in `worker/drive_sync.py:_run_drive_sync`:

1. `_list_files_for_folders` — mock mode returns `MOCK_FILES` fixtures; real mode pages `drive/v3/files?q='<folder>' in parents and trashed=false`, filters by `DRIVE_ALLOWED_MIME`, and **makes one extra `/permissions` call per file**.
2. `job.progress_total = len(files)`, then per file `_upsert_drive_file`:
   - Skip the *download/version* work if `file_cursors[external_id] == modifiedTime` **and** the doc is already `ready` → `progress_skipped++`. **Permissions are still re-resolved on this path** (they change without touching `modifiedTime`), grants are rewritten, and if the resolved ACL differs from the stored one a re-ingest of `current_version_id` is enqueued so the denormalised ACL fields in OpenSearch are rewritten too.
   - Download (Google-native docs are exported to `text/plain` / `text/csv`; binaries via `alt=media`), enforce `DRIVE_MAX_FILE_BYTES`.
   - **Map Drive permissions → Vridhi ACL** (below).
   - Upsert `Document` by `(tenant_id, source='google_drive', external_id)`, create a new `DocumentVersion`, replace grants, enqueue a **child `ingest` job**.
   - Update `file_cursors[external_id]`.
3. Per-file failures are caught, counted into `progress_failed`, and the loop continues — one bad file doesn't kill the sync.
4. Connection health is derived at the end: all-failed → `error` + `sync_failed`; some-failed → `degraded`; clean → `healthy`.
5. **Deletion propagation.** Only when the folder listing completed (a listing error fails the whole job before this point), any stored Drive document whose `external_id` is missing from the listing — deleted, trashed, moved out of a selected folder, or its folder un-selected — goes through `tombstone_document` (`services/tombstone.py`). If the listing did not complete, nothing is hidden: a Drive outage cannot wipe the index.

**Automatic sync.** Nobody has to click Sync Now any more. `IngestConsumer` calls `run_scheduler_tick` (`worker/scheduler.py`) from its poll loop once per `AUTO_SYNC_TICK_SECONDS` (default 60). For each connected Drive/Gmail connection it re-reads the row with `SELECT ... FOR UPDATE SKIP LOCKED` (so two workers never queue the same connection twice), applies the pure due-rule in `services/autosync.py:is_due` (interval `AUTO_SYNC_INTERVAL_SECONDS`, default 900; not switched off; not paused; no active job; Drive needs folders selected; at most `AUTO_SYNC_MAX_STARTS_PER_TICK`, default 5, are started per tick, never-synced and longest-waiting first, so a first deploy spreads over several ticks), and starts the sync through the *same* `start_sync` the button uses, with `trigger="schedule"` on the job (shown as "Automatic" vs "Manual" in the UI). An "active" job is a queued/running one whose `updated_at` moved within `AUTO_SYNC_STALE_AFTER_SECONDS` (default 3600); older ones, and a `syncing` connection with no fresh active job, are treated as abandoned by a dead worker and no longer block scheduling. State lives in `connections.config` (`auto_sync_enabled`, `auto_sync_failures`, `auto_sync_paused_reason`), toggled by `PUT /v1/connections/{google_drive|gmail}/auto-sync`. A sync counts as failed when its job goes `dead`, fails terminally under `QUEUE_BACKEND=db`, or completes with failures and nothing done or skipped; five in a row (`AUTO_SYNC_MAX_CONSECUTIVE_FAILURES`) pause auto-sync with a reason shown on the card. `AUTO_SYNC_ENABLED_GLOBAL=false` disables the scheduler entirely.
**How deletion is recorded.** `tombstone_document(db, search, doc, *, reason, actor_user_id, request_id)` is the one place that hides a document: it calls `search.delete_by_document` *first* (if that raises, the doc stays visible and is retried on the next sync), then sets `status=deleted`, `deleted_at`, `documents.deleted_reason` (`manual` for Vridhi's own Delete button, `source_removed` for propagation), and writes an audit event. The row is kept. If a hidden `source_removed` document reappears in Google, the next sync revives the **same row** (same document id) and re-ingests it; a `manual` deletion is never re-imported. Because retrieval trusts the index, not Postgres, this is also why Vridhi's own Delete now cleans OpenSearch (it used to hide only the Postgres row; see §8).

**Gmail** is the same idea with the History API (`services/gmail_history.py`): the first automatic run is a full pass that stores `connection.config["gmail_history_id"]`; later runs call `users.history.list` from that checkpoint and apply only the messages added/removed (deleted, or labelled TRASH/SPAM). If Google says the checkpoint is too old, the run falls back to a full pass. The checkpoint advances only after a fully successful batch, so a failure re-reads the same history next time.

### 5.3 The fail-closed ACL mapping

`DriveService.map_permissions_to_acl` (`drive.py:548`). Given the file's Drive permission list and the sync's default visibility:

| Situation | Result |
|---|---|
| Sync default is `private` | `private`, no grants — *the default is a ceiling, never widened* |
| Sync default is `selected` | `selected` + exactly the admin-chosen users |
| Default `org` **and** file has a `domain`/`anyone` permission | `org` (visible tenant-wide) |
| Default `org`, file shared to named users, ≥1 email matches an **active member** | `selected` + only the matched member IDs |
| Default `org`, file shared to named users, **no** email matches | `private` |
| Default `org`, **no** discoverable sharing metadata | `private` |

The fail-closed property is real and holds in three distinct ways: an unmatched external collaborator is silently dropped rather than granted; missing permission metadata (e.g. the `/permissions` call returned non-200, which the caller swallows into `permissions = []`) collapses to `private`, not `org`; and the requested default can only ever narrow, never widen.

**The caveats you should carry into any work on this:**

- **Identity matching is by email string only.** `func.lower(User.email) IN (...)`. A member whose Drive account uses a different address than their Vridhi account gets no access. There's no Google `sub`/account linkage between `oauth_accounts` and Drive permissions.
- **Drive roles are not honoured.** A Drive `commenter` and a Drive `writer` both map to the same read grant. Vridhi has no per-document read/write distinction anyway.
- **~~It's a point-in-time snapshot.~~ Fixed.** This used to read: *revoking someone in Drive doesn't revoke them in Vridhi until the file's `modifiedTime` changes and a sync runs — and sharing changes typically don't bump `modifiedTime`, so ACL revocations will routinely be missed.* That gap is closed: **every** sync re-reads the file's permission list and re-resolves the ACL, including for files whose content is unchanged, and pushes the result to Postgres *and* (via an enqueued re-ingest, only when the ACL actually changed) to the OpenSearch index. So a revocation now takes effect on the next sync, in both the documents API and search/RAG. What remains is that it's still **sync-triggered**, not push-triggered: there's no Drive change-notification channel, so the window is "until the next sync runs" — now at most one automatic-sync interval (15 minutes by default) rather than "until someone clicks Sync Now". Covered end-to-end by `scripts/smoke-phase-g.sh`, which asserts a group member loses both `GET /v1/documents/{id}` and `/v1/search` visibility after a revoke + re-sync with unchanged content.
- **Google Groups are honoured.** A `type: "group"` Drive permission is matched against the `groups` table populated by `services/groups.py`'s Workspace-Enterprise Admin SDK sync, producing `DocumentGroupGrant` rows. That sync's mock/live switch is `WORKSPACE_ENTERPRISE_MODE` (not `GOOGLE_DRIVE_MODE`), because the token it uses is the Admin-impersonated one.
- **Mapping migration.** `granted_group_ids` was added to the OpenSearch mapping after the index already existed in most environments, so `pipeline/index.py:ensure_index` issues an additive `PUT <index>/_mapping` on an existing index (idempotent, once per worker process) rather than only writing the mapping at index-creation time. Without it the field would arrive via dynamic mapping as analysed `text` and the `granted_group_ids` `terms` clause would silently never match. A field already present with the *wrong* type can't be fixed in place — `ensure_index` logs `opensearch.acl_field_wrong_type` and that index needs a reindex.
- **Vridhi admins bypass all of it** — `role_at_least(admin)` short-circuits both `can_access` and `acl_filter` before visibility is ever consulted. An org admin can read every Drive-synced document regardless of Drive permissions.
- **~~Deletions don't propagate.~~ Fixed.** This used to read: *a file deleted or unshared in Drive stays in Vridhi and stays answerable forever; there's no tombstone pass.* Now, after a Drive listing that **completed** (the guard: a listing error fails the job before the deletion step, so an outage or partial listing hides nothing), every stored file missing from the listing is tombstoned via `tombstone_document` (`services/tombstone.py`): index chunks deleted first, then `status=deleted` + `deleted_at` + `documents.deleted_reason` (`source_removed`, or `manual` for Vridhi's own Delete) + an audit event. The row is kept and revives as the same document if the file returns; `manual` deletions are never re-imported. Gmail does the same for deleted/trashed messages through the History checkpoint (§5.2). What's still true: an *unshared* file (access revoked, but the file still listed) is handled by the ACL re-resolution above, not by deletion; and a file the service account can still list but whose permissions call fails goes `private` rather than deleted. Covered end-to-end by `scripts/smoke-phase-h.sh`, which asserts at the `/v1/search` level.

---

## 6. What's genuinely done vs. stubbed

### Done and wired end-to-end

- Email/password auth, sessions (hashed tokens, revocation, TTL), password reset with full session revocation, email verification, invites with resend/revoke, RBAC with owner protections, audit log — all with an integration smoke test (`smoke-phase-a.sh`).
- Upload → S3/filesystem → parse (5 real parsers) → chunk → embed → OpenSearch, with retries, DLQ, and job status polling (`smoke-phase-b.sh`).
- Hybrid BM25+kNN retrieval with RRF, ACL-filtered on both legs, citations, feedback, conversation persistence (`smoke-phase-c.sh`).
- Drive OAuth + folder discovery + incremental sync + progress counters + fail-closed ACL (`smoke-phase-d.sh`).
- Alembic migrations for all four phases; error envelope + request IDs; structured JSON logging; CORS; health/readiness probes.

### Stand-ins — the complete list

| Flag / location | Value | What it actually does |
|---|---|---|
| `EMBEDDING_PROVIDER` | `hash` | **Not semantic.** `hash_embed` is a signed hash of tokens into 384 dims (`pipeline/process.py`, duplicated in `services/embeddings.py`). The kNN leg of "hybrid search" is a second lexical channel — it cannot match synonyms or paraphrases. |
| `LLM_PROVIDER` | `extractive` | No model. Sentence extraction + `[n]` markers (`generation.py:67`). |
| `GOOGLE_DRIVE_MODE` | `mock` | 3 folders / 4 hardcoded `.txt` files with fake permissions (one of them shared to a mock Google Group), defined inline at the top of `services/drive.py`. OAuth start redirects to the app's own callback. `scripts/smoke-phase-g.sh` mutates one fixture's `permissions` mid-run via the `apps/api/.mock-drive-overrides.json` side channel. |
| `WORKSPACE_ENTERPRISE_MODE` | `mock` | Domain-wide-delegation verification and the Admin SDK Groups sync both use fixtures (`services/groups.py` `MOCK_GROUPS` / `MOCK_GROUP_MEMBERS`). Independent of `GOOGLE_DRIVE_MODE` — don't conflate them. |
| `EMAIL_PROVIDER` | `log` | `services/email.py` logs subject + **full body** (including reset/invite links). Any other value — **including `smtp`** — raises `NotImplementedError`, despite `SMTP_HOST/PORT/USER/PASS` existing in `.env.example`. There is no SMTP implementation at all. |
| `TOKEN_BACKEND` | `fernet` | Works. `secrets_manager` is a `RuntimeError` stub (`services/tokens.py`). |
| `GOOGLE_CLIENT_ID/SECRET` | empty | Google *login* is off; `/v1/features.google_login_enabled` is false and the UI hides the button. |
| `GMAIL_ENABLED` | `false` | No code exists beyond the catalog entry. |
| `USAGE_ENABLED` / `BILLING_ENABLED` / `SSO_ENABLED` | `false` | `GET /v1/usage` hardcodes `{"available": false, "message": "Usage data isn't available yet."}`. No billing or SSO code at all. |
| `SEARCH_BACKEND=noop` | in `.env` | `NoopSearchIndex` silently discards every write. Docs reach `ready` but are unsearchable. |
| `WORKER_CONCURRENCY` | `2` | **Dead config.** Declared in `config.py:140`, referenced nowhere else. The consumer is strictly single-threaded. |
| `SESSION_SECRET` | `change-me-...` | Declared and length-validated but **never used** — sessions are random opaque tokens, nothing is signed with it. |
| `TOKEN_ENCRYPTION_KEY` | `vridhi-dev-...` | Committed dev default in `.env.example`. Must be replaced before any real Drive tokens are stored. |
| `DashboardOut.questions` | `0` | Hardcoded in `routers/workspace.py:get_dashboard` — the "questions asked" tile is always zero even though `messages` rows exist. |
| `chunks.page` / citation `page` | always `null` | Mapped in OpenSearch, read in `retrieval.py:185`, surfaced in the citation drawer — but **never written**. The chunker discards page/slide boundaries. The README's "page/chunk" citation claim is half-true: chunk index works, page never populates. |
| Conversation history UI | absent | `GET /v1/conversations` and `/v1/conversations/{id}` are implemented and tested server-side, but `frontend/src/api/workspace.ts` never calls them. Ask history is React state only — refresh loses it. |
| Rate limiting | absent | `frontend/src/api/client.ts` handles HTTP 429, but no rate limiter exists anywhere in the API. |
| `infra/iam` | notes only | A markdown table of planned roles. No Terraform/CDK. No deployment infrastructure of any kind in the repo. |

**Bottom line for your senior:** the *systems* work — multi-tenancy, ACLs, queueing, retries, versioning, idempotency — is real and reasonably careful. The *AI* work is scaffolded but unstarted: with the shipped defaults, Vridhi is a keyword search engine with citation formatting, not a RAG assistant. Setting `EMBEDDING_PROVIDER=openai` + `LLM_PROVIDER=openai` + keys is the switch, but note that flipping embeddings requires a **full re-index** (§8) and invalidates the tuned `MIN_CITATION_SCORE`.

---

## 7. Running it locally

### The path that works

```bash
cp .env.example .env
docker compose up --build          # postgres, localstack(s3+sqs), opensearch, api, worker
```

Compose's `environment:` block **overrides** the `.env` values for the three backend switches — `.env.example` ships `filesystem`/`db`/`noop`, but the API and worker containers both get `STORAGE_BACKEND=s3`, `QUEUE_BACKEND=sqs`, `SEARCH_BACKEND=opensearch`. That's intentional (the `.env` defaults are for running bare-metal without LocalStack), but it means **your `.env` does not describe what's running in Docker**. First real gotcha.

Startup order is enforced by healthchecks; the API container runs `alembic upgrade head` before uvicorn. LocalStack creates the bucket, queue, and DLQ (maxReceiveCount 5) via `infra/localstack/init-aws.sh`.

**The frontend is not in docker-compose.** Run it separately:

```bash
cd frontend && npm install && npm run dev   # :8443
```

**Second gotcha, and it will bite you immediately:** `frontend/src/api/client.ts` defaults `API_BASE` to `http://127.0.0.1:8001`, but the API listens on **8000**. There is no committed frontend `.env` (`.gitignore` excludes `.env*`). So a fresh clone's UI talks to a dead port. You need:

```bash
echo 'VITE_API_URL=http://localhost:8000' > frontend/.env.local
```

(`localhost`, not `127.0.0.1` — the API's CORS allowlist in `main.py` includes both, but `localhost:8443`→`localhost:8000` keeps the cookie origin consistent.)

Same 8000-vs-8001 inconsistency exists in `scripts/smoke-phase-a.sh` (defaults to 8001; b/c/d default to 8000).

### Verifying

```bash
curl localhost:8000/healthz      # {"status":"ok"}
curl localhost:8000/readyz       # {"status":"ready"}  ← actually pings Postgres
open  localhost:8000/docs        # OpenAPI, dev only

API_URL=http://localhost:8000 ./scripts/smoke-phase-a.sh   # auth/RBAC/invites
API_URL=http://localhost:8000 ./scripts/smoke-phase-b.sh   # upload→ingest→preview→delete
API_URL=http://localhost:8000 ./scripts/smoke-phase-c.sh   # search + chat + citations + no-answer
API_URL=http://localhost:8000 ./scripts/smoke-phase-d.sh   # Drive mock OAuth→folders→sync→disconnect
API_URL=http://localhost:8000 ./scripts/smoke-phase-g.sh   # Google Groups sharing + revoke-on-resync (docs API *and* search)
```

These are genuinely end-to-end: phase B uploads a real file and blocks until the worker reports `succeeded`; phase C asserts a citation exists *and* that an unanswerable question returns `no_answer`; phase D drives the whole Drive flow and waits for child ingest jobs to produce `ready` documents. They're bash + `python3` + `curl`, so on Windows run them from Git Bash or WSL.

Golden eval (needs a tenant seeded with the matching corpus — the smoke-C document is the seed):

```bash
API_URL=http://localhost:8000 EVAL_LIMIT=50 ./scripts/run-golden-eval.sh
```

200 rows in `evals/golden.jsonl`, roughly half `expect_answerable: false`. Be aware the pass criterion is lenient: an answerable question counts as PASS if the expected string appears **or** if any citation came back at all — so the score overstates answer quality.

Unit tests + lint (what CI runs — `.github/workflows/ci.yml`, API only, no worker or frontend job):

```bash
cd apps/api && ruff check . && pytest -q     # 5 tests: health, error shape, password, slug, roles
```

Running the worker outside Docker requires `PYTHONPATH=apps/api:apps/worker` (see `apps/worker/README.md`) because of the cross-package import described in §2.

---

## 8. Rough edges — read this before building on top

Ordered by how much damage they'd do.

**1. ~~Deleting a document does not remove it from OpenSearch.~~ Fixed; two residual gaps.** This used to be the worst item: `DocumentService.delete_document` only hid the Postgres row, so a deleted document's chunks stayed retrievable and citable by `/v1/search` and `/v1/chat` indefinitely. Delete (and Drive/Gmail deletion propagation) now goes through `tombstone_document`, which removes the chunks from the index before marking the row deleted (§5.2). Two things to know. (a) An ingest that is already in flight when the delete lands can still finish a fraction of a second later. It is guarded by re-checks in `process_ingest_job` (at entry, before the first index write, and after the writes, which cleans up after itself), **not by a lock**, so a tombstone that commits in the tiny window between the last check and the ingest's commit can leave text indexed until that document is re-ingested. A plain sync will not clean it up (the Drive deletion pass only scans documents that are not yet deleted), and a manually deleted document is never revived, so the stray text can persist. (b) Gmail change tracking through the History API is unproven against real Google: mock mode exercises the logic (checkpoint, deletion, full-pass fallback) but only a live mailbox can confirm the real record shapes and the expired-checkpoint 404 behaviour.

**2. ACL changes reach the index only from the Drive path.** `visibility`, `uploaded_by_user_id`, `granted_user_ids` and `granted_group_ids` are frozen into each OpenSearch doc at ingest, and `worker/ingest.py` is still the *only* writer of those fields. Drive sync now closes its own loop (it enqueues a re-ingest of the current version whenever a re-resolved ACL differs — §5.2), so Drive revocations do take effect in retrieval. But any *other* future "change who can see this" path — an in-app visibility editor, an admin re-grant, a group-membership change that isn't accompanied by a Drive re-sync — will still silently do nothing to retrieval unless it triggers re-indexing the same way. The clean long-term fix is still either to stop denormalising ACLs into the index and post-filter against Postgres, or to route every ACL mutation through one re-index-on-ACL-change helper. Note also that `granted_group_ids` needs the additive mapping migration in `pipeline/index.py:ensure_index` on any index created before this phase, or the group clause is inert (§5.3).

**3. Three implementations of one authorization rule.** `can_access` (imperative, `documents.py:119`), `accessible_filter` (SQLAlchemy, `:149`), and `acl_filter` (OpenSearch DSL, `retrieval.py:61`) all encode the same policy in three languages. They agree today. They will drift. If you touch visibility semantics, change all three — and extend `apps/api/tests/test_acl_agreement.py`, which is the one place that asserts the trio agrees (it checks all three honour group grants, all three short-circuit for admins, and that the `granted_group_ids` clause appears only when the caller actually has groups; it's pure — SQL is compiled, not executed, so no DB or cluster is needed). That test is deliberately *structural*, not exhaustive: it pins shape, not every branch of the policy.

**4. `incremental: false` doesn't force a full re-sync.** The flag is threaded from the request into `job.payload` and used only to null out `page_token` — but `_upsert_drive_file` (`drive_sync.py:276`) consults `conn.config["file_cursors"]` unconditionally. So "re-sync everything" still skips unchanged files. The Phase D smoke script passes `incremental: false` and appears to work only because the tenant is fresh. Also: `file_cursors` is an unbounded JSONB dict on `connections.config`, growing one key per Drive file forever.

**5. The `db` queue backend has no locking and no retry.** `poll_db_once` (`consumer.py:86`) does a plain `SELECT ... WHERE status='queued'` with no `FOR UPDATE SKIP LOCKED` — two worker replicas would process the same job twice (ingest is idempotent so it mostly survives, but the counters and audit trail wouldn't). Worse, the poll only ever selects `queued`, so a job that lands in `failed` is **never retried** — `attempt`/`max_attempts`/`dead` are effectively dead code in DB mode. All the real retry semantics live in the SQS path. Don't use `QUEUE_BACKEND=db` for anything but single-worker local dev.

**6. Blocking I/O in async routes.** `POST /v1/chat` is `async def`, but `RagService.ask` runs synchronous OpenSearch queries and — with `LLM_PROVIDER=openai` — a blocking `httpx.post` with a 90s timeout, all on the event loop. Under concurrent load this stalls the entire API process. Either make these routes `def` (FastAPI will threadpool them) or go async all the way down. Same applies to `_fetch_user_email`, the Drive token refresh, and folder listing.

**7. Streaming is theatre.** As described in §2 — the answer is fully computed and committed before the first SSE byte. The UI shows a typing effect that conveys nothing about real progress. If perceived latency matters, this needs actual token streaming from the provider (and the DB write moved to the end of the stream).

**8. Retrieval quality knobs are miscalibrated by construction.** `rerank_chunks` overwrites RRF scores with a differently-scaled composite, and `MIN_CITATION_SCORE=0.08` / the `0.12` floor in `_generate_extractive` are tuned against that composite. Swapping in real embeddings or a real reranker changes the score distribution and silently changes when the system says "I don't know". Any retrieval work must re-derive these thresholds — ideally against `evals/golden.jsonl` with a stricter scorer than the current one.

**9. Duplicated code across the API/worker boundary.** `hash_embed` and `openai_embed` exist verbatim in both `apps/api/app/services/embeddings.py` and `apps/worker/worker/pipeline/process.py`. **If these ever diverge, query vectors and document vectors land in different spaces and retrieval degrades silently** — no error, just worse results. Same shape of duplication for `ObjectStorage` (API version has write methods, worker version is read-only) and the two `Settings` classes. Consider a shared package.

**10. Drive sync is N+1 on the Google API.** `_list_files_for_folders` makes one `/permissions` request per file, serially, inside the listing loop. A 5,000-file Drive is 5,000+ sequential HTTPS calls in a single-threaded worker with no rate-limit handling, no backoff, and no 429 retry. It will hit Drive quotas. Also: `permissions` is requestable only by owners/writers — a non-200 is swallowed into `permissions = []`, which fails closed to `private` (safe, but means files will quietly become invisible with no diagnostic).

**11. No pagination or sorting guarantees on the chat path, and no conversation UI.** `list_conversations` caps at 50 with no cursor. Messages are loaded via `selectinload` with no limit — a long conversation loads entirely. And since the frontend never calls these endpoints, none of it is exercised.

**12. Smaller things worth knowing:**
- `save_selected_folders` (`drive.py`) calls `list_folders` on every save (a full paged Drive crawl in real mode) and contains a dead `if invalid ... pass` branch.
- The Drive OAuth callback depends on `SameSite=Lax` cookies surviving Google's redirect; hardening cookies to `Strict` would break connect.
- `EmailService` logs full message bodies including reset and invite links at INFO. Fine for `log` provider dev, but the log pipeline shouldn't outlive it.
- `Document.current_version_id` is a UUID column with no FK — nothing prevents it dangling.
- Ingest calls `search.index_chunk(..., refresh=True)` per chunk; a 500-chunk PDF triggers 500 index refreshes.
- `drive_sync` parent jobs report `succeeded` when children are *enqueued*, not *completed*, so "sync finished" in the UI doesn't mean "documents are searchable".
- The frontend is a Figma Make export (`frontend/AGENTS.md`, `package.json` name `figma-make-app`, `vite.config.ts` full of `figma*` plugins). It's a scaffold that happens to contain the real app — expect friction if you restructure it.
- No worker tests, no frontend tests, no CI job for either. API CI runs 5 unit tests that touch none of the ACL, retrieval, or ingest logic.

**13. ZIP expansion has no size/entry/nesting limits (Piece 4A scope cut, approved 2026-09-22).** `services/zip_expand.py` enforces path safety (zip-slip) but nothing else: a ZIP with an enormous entry, thousands of entries, or nested archives is expanded in full, in memory, on the worker, with no per-archive byte/count cap and no recursion into nested ZIPs (a `.zip` inside a `.zip` is skipped — `classify_document` doesn't recognize it — rather than expanded). A very large or maliciously crafted ZIP can consume excessive worker memory or time; there is also no corrupt-ZIP-specific error message beyond the generic "could not read zip" failure. Covered end-to-end (expansion, per-inner-file citation, partial and whole-ZIP removal) by `scripts/smoke-phase-i.sh`. Revisit before this is exposed to untrusted or general-availability traffic.

### If I were picking up the first ticket

Fix #3 (one authorization rule, three implementations) — index cleanup on delete (#1) used to be the obvious first ticket and is now done, and #3 is the next best way to learn this codebase: any change to visibility semantics forces you to touch all three and extend `test_acl_agreement.py`.

---

*Notes compiled by reading the source directly; every claim above is traceable to a cited file and line. No code was modified.*
