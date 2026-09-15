from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str) -> list[str]:
    return [part.strip().lower() for part in value.split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="vridhi-api", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    app_url: str = Field(default="http://localhost:8000", alias="APP_URL")
    frontend_url: str = Field(default="http://localhost:5173", alias="FRONTEND_URL")
    port: int = Field(default=8000, alias="PORT")
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+psycopg://vridhi:vridhi@localhost:5432/vridhi",
        alias="DATABASE_URL",
    )

    session_secret: str = Field(
        default="change-me-to-a-long-random-string-at-least-32-chars",
        alias="SESSION_SECRET",
        min_length=32,
    )
    session_cookie_name: str = Field(default="vridhi_session", alias="SESSION_COOKIE_NAME")
    session_ttl_days: int = Field(default=14, alias="SESSION_TTL_DAYS")
    password_reset_ttl_hours: int = Field(default=1, alias="PASSWORD_RESET_TTL_HOURS")
    email_verify_ttl_hours: int = Field(default=24, alias="EMAIL_VERIFY_TTL_HOURS")
    invite_ttl_days: int = Field(default=7, alias="INVITE_TTL_DAYS")
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")

    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    google_redirect_uri: str = Field(
        default="http://localhost:8000/v1/auth/google/callback",
        alias="GOOGLE_REDIRECT_URI",
    )

    email_provider: str = Field(default="log", alias="EMAIL_PROVIDER")
    email_from: str = Field(default="noreply@vridhi.ai", alias="EMAIL_FROM")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_pass: str = Field(default="", alias="SMTP_PASS")

    # --- Phase B: feature flags ---
    file_upload_enabled: bool = Field(default=True, alias="FILE_UPLOAD_ENABLED")
    google_drive_enabled: bool = Field(default=True, alias="GOOGLE_DRIVE_ENABLED")
    gmail_enabled: bool = Field(default=False, alias="GMAIL_ENABLED")
    ai_query_enabled: bool = Field(default=True, alias="AI_QUERY_ENABLED")
    search_enabled: bool = Field(default=True, alias="SEARCH_ENABLED")
    usage_enabled: bool = Field(default=False, alias="USAGE_ENABLED")
    billing_enabled: bool = Field(default=False, alias="BILLING_ENABLED")
    sso_enabled: bool = Field(default=False, alias="SSO_ENABLED")

    # --- Phase B: upload limits ---
    upload_max_bytes: int = Field(default=52_428_800, alias="UPLOAD_MAX_BYTES")  # 50 MiB
    upload_allowed_extensions: str = Field(
        default="pdf,docx,xlsx,pptx,txt,csv",
        alias="UPLOAD_ALLOWED_EXTENSIONS",
    )
    upload_allowed_mime: str = Field(
        default=(
            "application/pdf,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
            "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
            "text/plain,"
            "text/csv,"
            "application/octet-stream"
        ),
        alias="UPLOAD_ALLOWED_MIME",
    )
    document_preview_max_chars: int = Field(default=4000, alias="DOCUMENT_PREVIEW_MAX_CHARS")

    # --- Phase B: object storage (S3 / LocalStack / MinIO / filesystem) ---
    # storage_backend: s3 | filesystem
    storage_backend: str = Field(default="s3", alias="STORAGE_BACKEND")
    storage_local_path: str = Field(default="/tmp/vridhi-storage", alias="STORAGE_LOCAL_PATH")
    s3_endpoint_url: Optional[str] = Field(default="http://localhost:4566", alias="S3_ENDPOINT_URL")
    s3_bucket: str = Field(default="vridhi-documents", alias="S3_BUCKET")
    s3_access_key_id: str = Field(default="test", alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(default="test", alias="S3_SECRET_ACCESS_KEY")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")
    s3_force_path_style: bool = Field(default=True, alias="S3_FORCE_PATH_STYLE")
    s3_prefix: str = Field(default="tenants", alias="S3_PREFIX")

    # --- Phase B: queue (sqs | db) ---
    # db = worker polls sync_jobs table (no SQS required for local dev)
    queue_backend: str = Field(default="sqs", alias="QUEUE_BACKEND")
    sqs_endpoint_url: Optional[str] = Field(default="http://localhost:4566", alias="SQS_ENDPOINT_URL")
    sqs_region: str = Field(default="us-east-1", alias="SQS_REGION")
    sqs_access_key_id: str = Field(default="test", alias="SQS_ACCESS_KEY_ID")
    sqs_secret_access_key: str = Field(default="test", alias="SQS_SECRET_ACCESS_KEY")
    sqs_queue_name: str = Field(default="vridhi-ingest", alias="SQS_QUEUE_NAME")
    sqs_dlq_name: str = Field(default="vridhi-ingest-dlq", alias="SQS_DLQ_NAME")
    sqs_queue_url: Optional[str] = Field(default=None, alias="SQS_QUEUE_URL")
    sqs_dlq_url: Optional[str] = Field(default=None, alias="SQS_DLQ_URL")
    sqs_wait_time_seconds: int = Field(default=10, alias="SQS_WAIT_TIME_SECONDS")
    sqs_visibility_timeout: int = Field(default=300, alias="SQS_VISIBILITY_TIMEOUT")
    sqs_max_messages: int = Field(default=5, alias="SQS_MAX_MESSAGES")
    ingest_max_attempts: int = Field(default=5, alias="INGEST_MAX_ATTEMPTS")

    # --- Phase B: OpenSearch (opensearch | noop) ---
    search_backend: str = Field(default="opensearch", alias="SEARCH_BACKEND")
    opensearch_url: str = Field(default="http://localhost:9200", alias="OPENSEARCH_URL")
    opensearch_index: str = Field(default="vridhi-chunks", alias="OPENSEARCH_INDEX")
    opensearch_username: str = Field(default="", alias="OPENSEARCH_USERNAME")
    opensearch_password: str = Field(default="", alias="OPENSEARCH_PASSWORD")
    opensearch_use_ssl: bool = Field(default=False, alias="OPENSEARCH_USE_SSL")
    opensearch_verify_certs: bool = Field(default=False, alias="OPENSEARCH_VERIFY_CERTS")

    # --- Phase B: chunking ---
    chunk_size: int = Field(default=800, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=120, alias="CHUNK_OVERLAP")
    chunk_min_chars: int = Field(default=40, alias="CHUNK_MIN_CHARS")

    # --- Phase B: embeddings ---
    # provider: hash (local deterministic) | openai
    embedding_provider: str = Field(default="hash", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="hash-v1", alias="EMBEDDING_MODEL")
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_api_base: str = Field(default="https://api.openai.com/v1", alias="EMBEDDING_API_BASE")
    embedding_dimensions: int = Field(default=384, alias="EMBEDDING_DIMENSIONS")
    embedding_batch_size: int = Field(default=32, alias="EMBEDDING_BATCH_SIZE")

    # --- Phase B: worker knobs (also used by apps/worker) ---
    worker_poll_interval_seconds: float = Field(default=2.0, alias="WORKER_POLL_INTERVAL_SECONDS")
    worker_concurrency: int = Field(default=2, alias="WORKER_CONCURRENCY")

    # --- Phase C: hybrid retrieval + RAG ---
    retrieval_top_k: int = Field(default=40, alias="RETRIEVAL_TOP_K")
    retrieval_bm25_size: int = Field(default=40, alias="RETRIEVAL_BM25_SIZE")
    retrieval_knn_size: int = Field(default=40, alias="RETRIEVAL_KNN_SIZE")
    retrieval_rrf_k: int = Field(default=60, alias="RETRIEVAL_RRF_K")
    rerank_top_k: int = Field(default=8, alias="RERANK_TOP_K")
    min_citation_score: float = Field(default=0.08, alias="MIN_CITATION_SCORE")
    search_preview_chars: int = Field(default=280, alias="SEARCH_PREVIEW_CHARS")
    chat_max_context_chars: int = Field(default=12000, alias="CHAT_MAX_CONTEXT_CHARS")
    chat_stream_enabled: bool = Field(default=True, alias="CHAT_STREAM_ENABLED")
    # llm_provider: extractive (local grounded) | openai
    llm_provider: str = Field(default="extractive", alias="LLM_PROVIDER")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_api_base: str = Field(default="https://api.openai.com/v1", alias="LLM_API_BASE")
    llm_temperature: float = Field(default=0.1, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=800, alias="LLM_MAX_TOKENS")
    no_answer_phrase: str = Field(
        default="I don't know based on the available company knowledge.",
        alias="NO_ANSWER_PHRASE",
    )

    # --- Phase D: Google Drive ---
    # mock = local fixture sync without Google; oauth = real Google Drive
    google_drive_mode: str = Field(default="mock", alias="GOOGLE_DRIVE_MODE")
    google_drive_redirect_uri: str = Field(
        default="http://localhost:8000/v1/connections/google_drive/oauth/callback",
        alias="GOOGLE_DRIVE_REDIRECT_URI",
    )
    google_drive_scopes: str = Field(
        default="https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/userinfo.email",
        alias="GOOGLE_DRIVE_SCOPES",
    )
    # token_backend: fernet | secrets_manager (secrets_manager stub for later)
    token_backend: str = Field(default="fernet", alias="TOKEN_BACKEND")
    token_encryption_key: str = Field(
        default="vridhi-dev-fernet-key-change-me-32b!",
        alias="TOKEN_ENCRYPTION_KEY",
    )
    drive_sync_page_size: int = Field(default=100, alias="DRIVE_SYNC_PAGE_SIZE")
    drive_max_file_bytes: int = Field(default=52_428_800, alias="DRIVE_MAX_FILE_BYTES")
    drive_allowed_mime: str = Field(
        default=(
            "application/pdf,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
            "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
            "text/plain,"
            "text/csv,"
            "application/vnd.google-apps.document,"
            "application/vnd.google-apps.spreadsheet,"
            "application/vnd.google-apps.presentation"
        ),
        alias="DRIVE_ALLOWED_MIME",
    )

    # --- Phase F: Enterprise Google Workspace auth foundation ---
    # mock = fixture domain/directory listing, no real Google calls;
    # live = real Domain-Wide Delegation via a per-tenant service account.
    workspace_enterprise_mode: str = Field(default="mock", alias="WORKSPACE_ENTERPRISE_MODE")
    workspace_enterprise_scopes: str = Field(
        default="https://www.googleapis.com/auth/admin.directory.user.readonly",
        alias="WORKSPACE_ENTERPRISE_SCOPES",
    )

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        # Accept postgres:// from docker-compose / common tooling.
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://") :]
        if value.startswith("postgresql://") and "+psycopg" not in value:
            return "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

    @property
    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def allowed_upload_extensions(self) -> set[str]:
        return set(_split_csv(self.upload_allowed_extensions))

    @property
    def allowed_upload_mime(self) -> set[str]:
        return set(_split_csv(self.upload_allowed_mime))

    @property
    def object_storage_configured(self) -> bool:
        backend = self.storage_backend.lower().strip()
        if backend == "filesystem":
            return True
        return bool(self.s3_bucket and self.s3_access_key_id and self.s3_secret_access_key)

    @property
    def queue_configured(self) -> bool:
        backend = self.queue_backend.lower().strip()
        if backend == "db":
            return True
        return bool(self.sqs_queue_url or self.sqs_queue_name)

    @property
    def file_upload_ready(self) -> bool:
        return (
            self.file_upload_enabled
            and self.object_storage_configured
            and self.queue_configured
        )

    @property
    def search_ready(self) -> bool:
        return self.search_enabled and self.search_backend.lower().strip() == "opensearch"

    @property
    def ai_query_ready(self) -> bool:
        if not self.ai_query_enabled:
            return False
        if not self.search_ready:
            return False
        provider = self.llm_provider.lower().strip()
        if provider == "extractive":
            return True
        if provider == "openai":
            return bool(self.llm_api_key or self.embedding_api_key)
        return False

    @property
    def effective_llm_api_key(self) -> str:
        return self.llm_api_key or self.embedding_api_key

    @property
    def google_drive_ready(self) -> bool:
        if not self.google_drive_enabled:
            return False
        mode = self.google_drive_mode.lower().strip()
        if mode == "mock":
            return True
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def drive_allowed_mime_set(self) -> set[str]:
        return set(_split_csv(self.drive_allowed_mime))

    @property
    def drive_scope_list(self) -> list[str]:
        return [s for s in self.google_drive_scopes.split() if s.strip()]

    @property
    def workspace_enterprise_is_mock(self) -> bool:
        return self.workspace_enterprise_mode.lower().strip() == "mock"

    @property
    def workspace_enterprise_scope_list(self) -> list[str]:
        return [s for s in self.workspace_enterprise_scopes.split() if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
