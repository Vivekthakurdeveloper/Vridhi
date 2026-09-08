from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env", "../../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="vridhi-worker", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    database_url: str = Field(
        default="postgresql+psycopg://vridhi:vridhi@localhost:5432/vridhi",
        alias="DATABASE_URL",
    )

    storage_backend: str = Field(default="s3", alias="STORAGE_BACKEND")
    storage_local_path: str = Field(default="/tmp/vridhi-storage", alias="STORAGE_LOCAL_PATH")
    s3_endpoint_url: Optional[str] = Field(default="http://localhost:4566", alias="S3_ENDPOINT_URL")
    s3_bucket: str = Field(default="vridhi-documents", alias="S3_BUCKET")
    s3_access_key_id: str = Field(default="test", alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(default="test", alias="S3_SECRET_ACCESS_KEY")
    s3_region: str = Field(default="us-east-1", alias="S3_REGION")
    s3_force_path_style: bool = Field(default=True, alias="S3_FORCE_PATH_STYLE")
    s3_prefix: str = Field(default="tenants", alias="S3_PREFIX")

    queue_backend: str = Field(default="sqs", alias="QUEUE_BACKEND")
    sqs_endpoint_url: Optional[str] = Field(default="http://localhost:4566", alias="SQS_ENDPOINT_URL")
    sqs_region: str = Field(default="us-east-1", alias="SQS_REGION")
    sqs_access_key_id: str = Field(default="test", alias="SQS_ACCESS_KEY_ID")
    sqs_secret_access_key: str = Field(default="test", alias="SQS_SECRET_ACCESS_KEY")
    sqs_queue_name: str = Field(default="vridhi-ingest", alias="SQS_QUEUE_NAME")
    sqs_queue_url: Optional[str] = Field(default=None, alias="SQS_QUEUE_URL")
    sqs_wait_time_seconds: int = Field(default=10, alias="SQS_WAIT_TIME_SECONDS")
    sqs_visibility_timeout: int = Field(default=300, alias="SQS_VISIBILITY_TIMEOUT")
    sqs_max_messages: int = Field(default=5, alias="SQS_MAX_MESSAGES")

    search_backend: str = Field(default="opensearch", alias="SEARCH_BACKEND")
    opensearch_url: str = Field(default="http://localhost:9200", alias="OPENSEARCH_URL")
    opensearch_index: str = Field(default="vridhi-chunks", alias="OPENSEARCH_INDEX")
    opensearch_username: str = Field(default="", alias="OPENSEARCH_USERNAME")
    opensearch_password: str = Field(default="", alias="OPENSEARCH_PASSWORD")
    opensearch_use_ssl: bool = Field(default=False, alias="OPENSEARCH_USE_SSL")
    opensearch_verify_certs: bool = Field(default=False, alias="OPENSEARCH_VERIFY_CERTS")

    chunk_size: int = Field(default=800, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=120, alias="CHUNK_OVERLAP")
    chunk_min_chars: int = Field(default=40, alias="CHUNK_MIN_CHARS")

    embedding_provider: str = Field(default="hash", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="hash-v1", alias="EMBEDDING_MODEL")
    embedding_api_key: str = Field(default="", alias="EMBEDDING_API_KEY")
    embedding_api_base: str = Field(default="https://api.openai.com/v1", alias="EMBEDDING_API_BASE")
    embedding_dimensions: int = Field(default=384, alias="EMBEDDING_DIMENSIONS")
    embedding_batch_size: int = Field(default=32, alias="EMBEDDING_BATCH_SIZE")

    worker_poll_interval_seconds: float = Field(default=2.0, alias="WORKER_POLL_INTERVAL_SECONDS")
    ingest_max_attempts: int = Field(default=5, alias="INGEST_MAX_ATTEMPTS")

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://") :]
        if value.startswith("postgresql://") and "+psycopg" not in value:
            return "postgresql+psycopg://" + value[len("postgresql://") :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
