# Phase 1 backup approach (operational runbook)
#
# Postgres
# --------
# Daily logical dump (retain 14 days):
#   docker compose exec -T postgres pg_dump -U vridhi -d vridhi -Fc > backups/vridhi-$(date +%F).dump
# Restore:
#   docker compose exec -T postgres pg_restore -U vridhi -d vridhi --clean --if-exists < backups/vridhi-YYYY-MM-DD.dump
#
# Point-in-time recovery for production should use managed Postgres (RDS/Cloud SQL)
# with automated snapshots + WAL archiving. Local Docker volumes are NOT a backup.
#
# OpenSearch
# ----------
# Indices are rebuildable from Postgres (documents → versions → chunks → embeddings).
# Preferred recovery: reindex from source of truth rather than snapshot-only restore.
#
# Optional snapshot (staging/prod):
#   Register an S3 snapshot repository, then:
#     PUT _snapshot/<repo>/<snap>
#   Restore:
#     POST _snapshot/<repo>/<snap>/_restore
#
# Object storage (S3 / LocalStack)
# --------------------------------
# Enable versioning + lifecycle on the documents bucket in production.
# LocalStack data lives in the compose volume and should not be treated as durable.
#
# Secrets / tokens
# ----------------
# Connector tokens are Fernet-encrypted in Postgres (TOKEN_BACKEND=fernet).
# Rotate TOKEN_ENCRYPTION_KEY only with a re-encryption migration.
# Production should move to TOKEN_BACKEND=secrets_manager when available.
#
# Verification
# ------------
# After restore: alembic upgrade head → /readyz → smoke-phase-1-e2e.sh
# Orphan check: GET /v1/ops/orphans (admin)
