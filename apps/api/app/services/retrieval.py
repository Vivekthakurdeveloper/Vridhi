from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import UUID

from opensearchpy import OpenSearch, RequestsHttpConnection

from app.config import Settings
from app.security import MemberRole, role_at_least
from app.services.embeddings import embed_query

logger = logging.getLogger(__name__)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    version_id: str
    title: str
    content: str
    chunk_index: int
    visibility: str
    score: float
    page: Optional[int] = None
    source_url: Optional[str] = None
    bm25_rank: Optional[int] = None
    knn_rank: Optional[int] = None


@dataclass
class HybridResult:
    items: list[RetrievedChunk] = field(default_factory=list)


class OpenSearchRetriever:
    def __init__(self, settings: Settings):
        self.settings = settings
        parsed = urlparse(settings.opensearch_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 9200)
        http_auth = (
            (settings.opensearch_username, settings.opensearch_password)
            if settings.opensearch_username
            else None
        )
        self.index = settings.opensearch_index
        self.client = OpenSearch(
            hosts=[{"host": host, "port": port}],
            http_auth=http_auth,
            use_ssl=settings.opensearch_use_ssl or parsed.scheme == "https",
            verify_certs=settings.opensearch_verify_certs,
            connection_class=RequestsHttpConnection,
            timeout=30,
        )

    def acl_filter(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
        user_group_ids: Optional[set[UUID]] = None,
    ) -> dict[str, Any]:
        must: list[dict[str, Any]] = [{"term": {"tenant_id": str(tenant_id)}}]
        if role_at_least(role, MemberRole.admin):
            return {"bool": {"must": must}}
        uid = str(user_id)
        group_uids = [str(g) for g in (user_group_ids or set())]
        selected_should: list[dict[str, Any]] = [{"term": {"granted_user_ids": uid}}]
        if group_uids:
            selected_should.append({"terms": {"granted_group_ids": group_uids}})
        acl = {
            "bool": {
                "should": [
                    {"term": {"uploaded_by_user_id": uid}},
                    {"term": {"visibility": "org"}},
                    {
                        "bool": {
                            "must": [
                                {"term": {"visibility": "selected"}},
                                {"bool": {"should": selected_should, "minimum_should_match": 1}},
                            ]
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        }
        must.append(acl)
        return {"bool": {"must": must}}

    def hybrid_search(
        self,
        *,
        query: str,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
        size: Optional[int] = None,
        user_group_ids: Optional[set[UUID]] = None,
    ) -> list[RetrievedChunk]:
        filt = self.acl_filter(tenant_id=tenant_id, user_id=user_id, role=role, user_group_ids=user_group_ids)
        bm25_size = self.settings.retrieval_bm25_size
        knn_size = self.settings.retrieval_knn_size
        rrf_k = self.settings.retrieval_rrf_k
        top_n = size or self.settings.retrieval_top_k

        bm25_body = {
            "size": bm25_size,
            "query": {
                "bool": {
                    "filter": filt,
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["title^2", "content"],
                                "type": "best_fields",
                            }
                        }
                    ],
                }
            },
            "_source": {
                "excludes": ["embedding"],
            },
        }
        vector = embed_query(self.settings, query)
        knn_body = {
            "size": knn_size,
            "query": {
                "bool": {
                    "filter": filt,
                    "must": [
                        {
                            "knn": {
                                "embedding": {
                                    "vector": vector,
                                    "k": knn_size,
                                }
                            }
                        }
                    ],
                }
            },
            "_source": {"excludes": ["embedding"]},
        }

        bm25_hits = self.client.search(index=self.index, body=bm25_body).get("hits", {}).get("hits", [])
        knn_hits = self.client.search(index=self.index, body=knn_body).get("hits", {}).get("hits", [])

        fused: dict[str, RetrievedChunk] = {}
        rrf_scores: dict[str, float] = {}

        for rank, hit in enumerate(bm25_hits, start=1):
            chunk = self._hit_to_chunk(hit, bm25_rank=rank)
            fused[chunk.chunk_id] = chunk
            rrf_scores[chunk.chunk_id] = rrf_scores.get(chunk.chunk_id, 0.0) + 1.0 / (rrf_k + rank)

        for rank, hit in enumerate(knn_hits, start=1):
            chunk = self._hit_to_chunk(hit, knn_rank=rank)
            if chunk.chunk_id in fused:
                existing = fused[chunk.chunk_id]
                existing.knn_rank = rank
                if chunk.page is not None:
                    existing.page = chunk.page
                if chunk.source_url:
                    existing.source_url = chunk.source_url
            else:
                fused[chunk.chunk_id] = chunk
            rrf_scores[chunk.chunk_id] = rrf_scores.get(chunk.chunk_id, 0.0) + 1.0 / (rrf_k + rank)

        ranked = sorted(fused.values(), key=lambda c: rrf_scores.get(c.chunk_id, 0.0), reverse=True)
        for item in ranked:
            item.score = rrf_scores.get(item.chunk_id, 0.0)
        return ranked[:top_n]

    def _hit_to_chunk(
        self,
        hit: dict[str, Any],
        *,
        bm25_rank: Optional[int] = None,
        knn_rank: Optional[int] = None,
    ) -> RetrievedChunk:
        src = hit.get("_source") or {}
        meta_page = src.get("page")
        return RetrievedChunk(
            chunk_id=str(src.get("chunk_id") or hit.get("_id")),
            document_id=str(src.get("document_id") or ""),
            version_id=str(src.get("version_id") or ""),
            title=str(src.get("title") or "Untitled"),
            content=str(src.get("content") or ""),
            chunk_index=int(src.get("chunk_index") or 0),
            visibility=str(src.get("visibility") or "private"),
            score=float(hit.get("_score") or 0.0),
            page=int(meta_page) if meta_page is not None else None,
            source_url=src.get("source_url"),
            bm25_rank=bm25_rank,
            knn_rank=knn_rank,
        )


def rerank_chunks(query: str, chunks: list[RetrievedChunk], top_k: int) -> list[RetrievedChunk]:
    """Lightweight lexical rerank on top of RRF scores (no external model required)."""
    q_tokens = set(re_tokens(query))
    if not q_tokens:
        return chunks[:top_k]

    scored: list[tuple[float, RetrievedChunk]] = []
    for chunk in chunks:
        c_tokens = re_tokens(chunk.content)
        if not c_tokens:
            overlap = 0.0
        else:
            overlap = len(q_tokens & set(c_tokens)) / max(len(q_tokens), 1)
        title_bonus = 0.15 if any(t in chunk.title.lower() for t in q_tokens) else 0.0
        combined = chunk.score + overlap + title_bonus
        scored.append((combined, chunk))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    out: list[RetrievedChunk] = []
    for score, chunk in scored[:top_k]:
        chunk.score = score
        out.append(chunk)
    return out


def re_tokens(text: str) -> list[str]:
    import re

    return re.findall(r"[a-z0-9]{2,}", text.lower())


@lru_cache
def get_retriever() -> OpenSearchRetriever:
    from app.config import get_settings

    return OpenSearchRetriever(get_settings())
