from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.errors import AppError
from app.models import (
    AnswerFeedback,
    Conversation,
    Document,
    Message,
    MessageCitation,
)
from app.security import FeedbackRating, MemberRole, MessageRole, utcnow
from app.services import groups as groups_service
from app.services.generation import generate_answer, stream_answer_tokens
from app.services.retrieval import OpenSearchRetriever, rerank_chunks

logger = logging.getLogger(__name__)


@dataclass
class SearchHitOut:
    document_id: str
    chunk_id: str
    title: str
    preview: str
    score: float
    page: Optional[int]
    chunk_index: int
    source_url: Optional[str]


class RagService:
    def __init__(self, db: Session, settings: Settings, retriever: OpenSearchRetriever):
        self.db = db
        self.settings = settings
        self.retriever = retriever

    def require_search(self) -> None:
        if not self.settings.search_ready:
            raise AppError(
                "SEARCH_NOT_AVAILABLE",
                "Search isn't available yet.",
                501,
            )

    def require_ai(self) -> None:
        if not self.settings.ai_query_ready:
            raise AppError(
                "AI_NOT_AVAILABLE",
                "AI search isn't available yet.",
                501,
            )

    def search(
        self,
        *,
        query: str,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
        limit: int = 10,
    ) -> list[SearchHitOut]:
        self.require_search()
        q = query.strip()
        if not q:
            raise AppError("VALIDATION_ERROR", "Query is required.", 400)
        user_group_ids = groups_service.user_group_ids(self.db, user_id)
        retrieved = self.retriever.hybrid_search(
            query=q, tenant_id=tenant_id, user_id=user_id, role=role, user_group_ids=user_group_ids
        )
        ranked = rerank_chunks(q, retrieved, top_k=max(limit, self.settings.rerank_top_k))
        url_map = self._source_urls([c.document_id for c in ranked], tenant_id)
        preview_chars = self.settings.search_preview_chars
        hits: list[SearchHitOut] = []
        for chunk in ranked[:limit]:
            text = chunk.content.strip()
            preview = text if len(text) <= preview_chars else text[: preview_chars - 1].rstrip() + "…"
            hits.append(
                SearchHitOut(
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    title=chunk.title,
                    preview=preview,
                    score=round(chunk.score, 4),
                    page=chunk.page,
                    chunk_index=chunk.chunk_index,
                    source_url=chunk.source_url or url_map.get(chunk.document_id),
                )
            )
        return hits

    def get_or_create_conversation(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        conversation_id: Optional[UUID],
        title: Optional[str],
    ) -> Conversation:
        if conversation_id:
            convo = self.db.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                    Conversation.user_id == user_id,
                )
            )
            if not convo:
                raise AppError("CONVERSATION_NOT_FOUND", "Conversation not found.", 404)
            return convo
        convo = Conversation(
            id=uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            title=(title or "New conversation")[:120],
        )
        self.db.add(convo)
        self.db.flush()
        return convo

    def ask(
        self,
        *,
        query: str,
        tenant_id: UUID,
        user_id: UUID,
        role: MemberRole,
        conversation_id: Optional[UUID] = None,
    ) -> tuple[Conversation, Message, Message, list[MessageCitation]]:
        self.require_ai()
        q = query.strip()
        if not q:
            raise AppError("VALIDATION_ERROR", "Query is required.", 400)

        convo = self.get_or_create_conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            title=q[:80],
        )
        if not convo.title or convo.title == "New conversation":
            convo.title = q[:80]

        user_msg = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=convo.id,
            role=MessageRole.user,
            content=q,
        )
        self.db.add(user_msg)
        self.db.flush()

        user_group_ids = groups_service.user_group_ids(self.db, user_id)
        retrieved = self.retriever.hybrid_search(
            query=q, tenant_id=tenant_id, user_id=user_id, role=role, user_group_ids=user_group_ids
        )
        ranked = rerank_chunks(q, retrieved, top_k=self.settings.rerank_top_k)
        generation = generate_answer(self.settings, query=q, chunks=ranked)

        assistant = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=convo.id,
            role=MessageRole.assistant,
            content=generation.answer,
            no_answer=generation.no_answer,
            model=generation.model,
        )
        self.db.add(assistant)
        self.db.flush()

        citations: list[MessageCitation] = []
        if not generation.no_answer:
            url_map = self._source_urls(
                [c.document_id for c in generation.citations], tenant_id
            )
            for rank, chunk in enumerate(generation.citations, start=1):
                try:
                    doc_uuid = UUID(chunk.document_id) if chunk.document_id else None
                except ValueError:
                    doc_uuid = None
                try:
                    chunk_uuid = UUID(chunk.chunk_id) if chunk.chunk_id else None
                except ValueError:
                    chunk_uuid = None
                row = MessageCitation(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    message_id=assistant.id,
                    document_id=doc_uuid,
                    chunk_id=chunk_uuid,
                    rank=rank,
                    title=chunk.title,
                    passage=chunk.content[:2000],
                    page=chunk.page,
                    chunk_index=chunk.chunk_index,
                    score=chunk.score,
                    source_url=chunk.source_url or url_map.get(chunk.document_id),
                )
                self.db.add(row)
                citations.append(row)

        convo.updated_at = utcnow()
        self.db.commit()
        self.db.refresh(convo)
        self.db.refresh(assistant)
        for c in citations:
            self.db.refresh(c)
        return convo, user_msg, assistant, citations

    def list_conversations(self, *, tenant_id: UUID, user_id: UUID) -> list[Conversation]:
        return list(
            self.db.scalars(
                select(Conversation)
                .where(Conversation.tenant_id == tenant_id, Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.desc())
                .limit(50)
            ).all()
        )

    def get_conversation(
        self, *, conversation_id: UUID, tenant_id: UUID, user_id: UUID
    ) -> Conversation:
        convo = self.db.scalar(
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.user_id == user_id,
            )
            .options(
                selectinload(Conversation.messages).selectinload(Message.citations),
                selectinload(Conversation.messages).selectinload(Message.feedback),
            )
        )
        if not convo:
            raise AppError("CONVERSATION_NOT_FOUND", "Conversation not found.", 404)
        return convo

    def save_feedback(
        self,
        *,
        message_id: UUID,
        tenant_id: UUID,
        user_id: UUID,
        rating: FeedbackRating,
        comment: Optional[str] = None,
    ) -> AnswerFeedback:
        msg = self.db.scalar(
            select(Message).where(Message.id == message_id, Message.tenant_id == tenant_id)
        )
        if not msg or msg.role != MessageRole.assistant:
            raise AppError("MESSAGE_NOT_FOUND", "Assistant message not found.", 404)
        convo = self.db.get(Conversation, msg.conversation_id)
        if not convo or convo.user_id != user_id:
            raise AppError("FORBIDDEN", "You cannot rate this answer.", 403)

        existing = self.db.scalar(
            select(AnswerFeedback).where(
                AnswerFeedback.message_id == message_id,
                AnswerFeedback.user_id == user_id,
            )
        )
        if existing:
            existing.rating = rating
            existing.comment = comment
            existing.updated_at = utcnow()
            self.db.commit()
            self.db.refresh(existing)
            return existing

        row = AnswerFeedback(
            id=uuid4(),
            tenant_id=tenant_id,
            message_id=message_id,
            user_id=user_id,
            rating=rating,
            comment=comment,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _source_urls(self, document_ids: list[str], tenant_id: UUID) -> dict[str, Optional[str]]:
        uuids: list[UUID] = []
        for raw in document_ids:
            try:
                uuids.append(UUID(raw))
            except ValueError:
                continue
        if not uuids:
            return {}
        rows = self.db.scalars(
            select(Document).where(Document.tenant_id == tenant_id, Document.id.in_(uuids))
        ).all()
        return {str(row.id): row.source_url for row in rows}


def iter_stream_tokens(answer: str):
    yield from stream_answer_tokens(answer)
