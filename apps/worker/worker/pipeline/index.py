from __future__ import annotations

import logging
from typing import Any, Optional, Protocol
from urllib.parse import urlparse

from opensearchpy import OpenSearch, RequestsHttpConnection

logger = logging.getLogger(__name__)

# ACL fields that MUST be `keyword` for the exact-match term/terms clauses in
# apps/api/app/services/retrieval.py's acl_filter to work. Indices created by an
# earlier phase predate `granted_group_ids`, and OpenSearch would otherwise
# dynamically map it as analysed `text`, against which a `terms` query on a
# hyphenated UUID never matches -- i.e. the group clause would be silently
# inert. ensure_index() therefore also runs this as an additive mapping
# migration on an already-existing index.
ACL_KEYWORD_PROPERTIES: dict[str, dict[str, str]] = {
    "granted_user_ids": {"type": "keyword"},
    "granted_group_ids": {"type": "keyword"},
}


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
        self._acl_mapping_checked = False
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
            self._ensure_acl_mapping()
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
        self._acl_mapping_checked = True
        logger.info("opensearch.index_created", extra={"operation": "ensure_index"})

    def _ensure_acl_mapping(self) -> None:
        """Add any missing ACL keyword properties to an already-existing index.

        Idempotent and safe to call on every ensure_index(): it only issues
        `PUT <index>/_mapping` for properties that are absent, and a property
        that already exists with the right type is left alone. A property that
        exists with the *wrong* type cannot be changed in place by
        Elasticsearch/OpenSearch -- that needs a reindex -- so we log loudly
        instead of failing ingestion.
        """
        if self._acl_mapping_checked:
            return
        try:
            current = self.client.indices.get_mapping(index=self.index)
        except Exception:
            logger.warning(
                "opensearch.get_mapping_failed", extra={"operation": "ensure_index"}
            )
            return  # transient: retry on the next ensure_index()

        props: dict[str, Any] = {}
        for entry in (current or {}).values():
            props = ((entry or {}).get("mappings") or {}).get("properties") or {}
            break

        missing = {
            name: spec for name, spec in ACL_KEYWORD_PROPERTIES.items() if name not in props
        }
        mistyped = [
            name
            for name, spec in ACL_KEYWORD_PROPERTIES.items()
            if name in props and (props[name] or {}).get("type") != spec["type"]
        ]
        if missing:
            try:
                self.client.indices.put_mapping(
                    index=self.index, body={"properties": missing}
                )
            except Exception:
                logger.exception(
                    "opensearch.mapping_update_failed",
                    extra={"operation": "ensure_index"},
                )
                return  # retry on the next ensure_index()
            logger.info(
                "opensearch.mapping_updated",
                extra={"operation": "ensure_index", "request_id": ",".join(sorted(missing))},
            )
        if mistyped:
            # Security-relevant: an analysed `text` ACL field makes the matching
            # term/terms clause in acl_filter silently never match, so grants of
            # that kind stop being honoured at retrieval time. Requires a manual
            # reindex of the OpenSearch index to repair.
            logger.error(
                "opensearch.acl_field_wrong_type",
                extra={
                    "operation": "ensure_index",
                    "request_id": ",".join(sorted(mistyped)),
                },
            )
        self._acl_mapping_checked = True

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
