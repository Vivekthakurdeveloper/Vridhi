from __future__ import annotations

import logging
from typing import Any, Optional, Protocol
from urllib.parse import urlparse

from opensearchpy import OpenSearch, RequestsHttpConnection

logger = logging.getLogger(__name__)


class SearchIndex(Protocol):
    def ensure_index(self) -> None: ...
    def index_chunk(self, doc_id: str, body: dict[str, Any]) -> None: ...
    def delete_by_document(self, tenant_id: str, document_id: str) -> None: ...


class OpenSearchIndex:
    def __init__(
        self,
        *,
        url: str,
        index: str,
        username: str = "",
        password: str = "",
        use_ssl: bool = False,
        verify_certs: bool = False,
        dimensions: int = 384,
    ):
        parsed = urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 9200)
        http_auth = (username, password) if username else None
        self.index = index
        self.dimensions = dimensions
        self.client = OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_auth=http_auth,
            use_ssl=use_ssl or parsed.scheme == "https",
            verify_certs=verify_certs,
            connection_class=RequestsHttpConnection,
            timeout=30,
        )

    def ensure_index(self) -> None:
        if self.client.indices.exists(index=self.index):
            return
        body = {
            "settings": {"index": {"knn": True, "number_of_shards": 1, "number_of_replicas": 0}},
            "mappings": {
                "properties": {
                    "tenant_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "version_id": {"type": "keyword"},
                    "chunk_id": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                    "title": {"type": "text"},
                    "content": {"type": "text"},
                    "visibility": {"type": "keyword"},
                    "uploaded_by_user_id": {"type": "keyword"},
                    "granted_user_ids": {"type": "keyword"},
                    "granted_group_ids": {"type": "keyword"},
                    "source_url": {"type": "keyword"},
                    "page": {"type": "integer"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": self.dimensions,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "nmslib",
                        },
                    },
                }
            },
        }
        self.client.indices.create(index=self.index, body=body)
        logger.info("opensearch.index_created", extra={"operation": "ensure_index"})

    def index_chunk(self, doc_id: str, body: dict[str, Any]) -> None:
        self.client.index(index=self.index, id=doc_id, body=body, refresh=True)

    def delete_by_document(self, tenant_id: str, document_id: str) -> None:
        self.client.delete_by_query(
            index=self.index,
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"tenant_id": tenant_id}},
                            {"term": {"document_id": document_id}},
                        ]
                    }
                }
            },
            refresh=True,
            ignore_unavailable=True,
        )


class NoopSearchIndex:
    def ensure_index(self) -> None:
        return None

    def index_chunk(self, doc_id: str, body: dict[str, Any]) -> None:
        _ = (doc_id, body)

    def delete_by_document(self, tenant_id: str, document_id: str) -> None:
        _ = (tenant_id, document_id)


def build_search_index(
    *,
    backend: str,
    url: str,
    index: str,
    username: str = "",
    password: str = "",
    use_ssl: bool = False,
    verify_certs: bool = False,
    dimensions: int = 384,
) -> SearchIndex:
    name = backend.lower().strip()
    if name == "noop":
        return NoopSearchIndex()
    if name == "opensearch":
        return OpenSearchIndex(
            url=url,
            index=index,
            username=username,
            password=password,
            use_ssl=use_ssl,
            verify_certs=verify_certs,
            dimensions=dimensions,
        )
    raise RuntimeError(f"Unknown SEARCH_BACKEND: {backend}")
