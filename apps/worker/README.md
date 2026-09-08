# Sync / embedding worker

Consumes ingest jobs from SQS, then:

1. Downloads the file from S3
2. Parses PDF / DOCX / XLSX / PPTX / TXT / CSV
3. Chunks text (configurable `CHUNK_SIZE` / `CHUNK_OVERLAP`)
4. Embeds (`EMBEDDING_PROVIDER=hash` locally, or `openai`)
5. Indexes into OpenSearch and writes chunk / embedding meta rows

## Run locally

```bash
# infra
docker compose up -d postgres localstack opensearch

# from repo root
export PYTHONPATH=apps/api:apps/worker
cd apps/worker
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m worker
```

Or via Compose:

```bash
docker compose up --build worker
```

All settings are env-driven — see root `.env.example`.
