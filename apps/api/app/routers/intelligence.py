from __future__ import annotations

import json
from typing import Annotated, AsyncIterator, Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import RequestContext, require_tenant
from app.models import Conversation, Message, MessageCitation
from app.schemas import (
    ChatRequest,
    ChatResponse,
    CitationOut,
    ConversationOut,
    FeedbackOut,
    FeedbackRequest,
    MessageOut,
    SearchHitOut,
    SearchRequest,
    SearchResponse,
)
from app.security import FeedbackRating
from app.services.rag import RagService, iter_stream_tokens
from app.services.retrieval import get_retriever

router = APIRouter()


def get_rag_service(
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RagService:
    return RagService(db, settings, get_retriever())


def _citation_out(row: MessageCitation) -> CitationOut:
    return CitationOut(
        id=row.id,
        rank=row.rank,
        document_id=row.document_id,
        chunk_id=row.chunk_id,
        title=row.title,
        passage=row.passage,
        page=row.page,
        chunk_index=row.chunk_index,
        score=row.score,
        source_url=row.source_url,
    )


def _message_out(msg: Message) -> MessageOut:
    feedback = None
    if getattr(msg, "feedback", None) is not None:
        feedback = msg.feedback.rating.value
    return MessageOut(
        id=msg.id,
        role=msg.role.value if hasattr(msg.role, "value") else str(msg.role),
        content=msg.content,
        no_answer=bool(msg.no_answer),
        model=msg.model,
        created_at=msg.created_at,
        citations=[_citation_out(c) for c in sorted(msg.citations or [], key=lambda x: x.rank)],
        feedback=feedback,
    )


def _conversation_out(convo: Conversation, include_messages: bool = False) -> ConversationOut:
    messages: list[MessageOut] = []
    if include_messages:
        ordered = sorted(convo.messages or [], key=lambda m: m.created_at)
        messages = [_message_out(m) for m in ordered]
    return ConversationOut(
        id=convo.id,
        title=convo.title,
        created_at=convo.created_at,
        updated_at=convo.updated_at,
        messages=messages,
    )


@router.post("/v1/search", response_model=SearchResponse)
def search(
    body: SearchRequest,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    rag: Annotated[RagService, Depends(get_rag_service)],
) -> SearchResponse:
    assert ctx.membership and ctx.user
    hits = rag.search(
        query=body.query,
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        role=ctx.membership.role,
        limit=body.limit,
    )
    results: list[SearchHitOut] = []
    for hit in hits:
        results.append(
            SearchHitOut(
                document_id=UUID(hit.document_id),
                chunk_id=UUID(hit.chunk_id),
                title=hit.title,
                preview=hit.preview,
                score=hit.score,
                page=hit.page,
                chunk_index=hit.chunk_index,
                source_url=hit.source_url,
            )
        )
    return SearchResponse(query=body.query, total=len(results), results=results)


@router.post("/v1/chat")
async def chat(
    body: ChatRequest,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    rag: Annotated[RagService, Depends(get_rag_service)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    assert ctx.membership and ctx.user
    want_stream = body.stream and settings.chat_stream_enabled
    convo, user_msg, assistant, citations = rag.ask(
        query=body.query,
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        role=ctx.membership.role,
        conversation_id=body.conversation_id,
    )
    assistant.citations = citations

    if not want_stream:
        return ChatResponse(
            conversation_id=convo.id,
            user_message=_message_out(user_msg),
            assistant_message=_message_out(assistant),
        )

    async def event_stream() -> AsyncIterator[str]:
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        yield sse(
            "meta",
            {
                "conversation_id": str(convo.id),
                "user_message_id": str(user_msg.id),
                "assistant_message_id": str(assistant.id),
                "no_answer": assistant.no_answer,
                "model": assistant.model,
            },
        )
        for cite in citations:
            yield sse("citation", _citation_out(cite).model_dump(mode="json"))

        if assistant.no_answer:
            yield sse(
                "no_answer",
                {"message": assistant.content, "message_id": str(assistant.id)},
            )
        else:
            for token in iter_stream_tokens(assistant.content):
                yield sse("token", {"text": token})
            yield sse(
                "done",
                {
                    "conversation_id": str(convo.id),
                    "message_id": str(assistant.id),
                    "content": assistant.content,
                },
            )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/v1/conversations", response_model=list[ConversationOut])
def list_conversations(
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    rag: Annotated[RagService, Depends(get_rag_service)],
) -> list[ConversationOut]:
    assert ctx.membership and ctx.user
    rows = rag.list_conversations(tenant_id=ctx.membership.tenant_id, user_id=ctx.user.id)
    return [_conversation_out(row) for row in rows]


@router.get("/v1/conversations/{conversation_id}", response_model=ConversationOut)
def get_conversation(
    conversation_id: UUID,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    rag: Annotated[RagService, Depends(get_rag_service)],
) -> ConversationOut:
    assert ctx.membership and ctx.user
    convo = rag.get_conversation(
        conversation_id=conversation_id,
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
    )
    return _conversation_out(convo, include_messages=True)


@router.post("/v1/messages/{message_id}/feedback", response_model=FeedbackOut)
def post_feedback(
    message_id: UUID,
    body: FeedbackRequest,
    ctx: Annotated[RequestContext, Depends(require_tenant)],
    rag: Annotated[RagService, Depends(get_rag_service)],
) -> FeedbackOut:
    assert ctx.membership and ctx.user
    row = rag.save_feedback(
        message_id=message_id,
        tenant_id=ctx.membership.tenant_id,
        user_id=ctx.user.id,
        rating=FeedbackRating(body.rating),
        comment=body.comment,
    )
    return FeedbackOut(
        id=row.id,
        message_id=row.message_id,
        rating=row.rating.value,
        comment=row.comment,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
