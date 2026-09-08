from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterator, Optional

import httpx

from app.config import Settings
from app.services.retrieval import RetrievedChunk

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    answer: str
    no_answer: bool
    model: str
    citations: list[RetrievedChunk]


def _build_context(chunks: list[RetrievedChunk], max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    for idx, chunk in enumerate(chunks, start=1):
        header = f"[{idx}] {chunk.title}"
        if chunk.page is not None:
            header += f" (page {chunk.page})"
        block = f"{header}\n{chunk.content.strip()}"
        if used + len(block) > max_chars and parts:
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts)


def generate_answer(
    settings: Settings,
    *,
    query: str,
    chunks: list[RetrievedChunk],
) -> GenerationResult:
    min_score = settings.min_citation_score
    usable = [c for c in chunks if c.score >= min_score and c.content.strip()]
    if not usable:
        return GenerationResult(
            answer=settings.no_answer_phrase,
            no_answer=True,
            model=settings.llm_provider,
            citations=[],
        )

    provider = settings.llm_provider.lower().strip()
    if provider == "openai":
        return _generate_openai(settings, query=query, chunks=usable)
    return _generate_extractive(settings, query=query, chunks=usable)


def stream_answer_tokens(answer: str, *, chunk_chars: int = 24) -> Iterator[str]:
    text = answer or ""
    for i in range(0, len(text), chunk_chars):
        yield text[i : i + chunk_chars]


def _generate_extractive(
    settings: Settings,
    *,
    query: str,
    chunks: list[RetrievedChunk],
) -> GenerationResult:
    """Grounded local answerer: quotes best passages; refuses when weak evidence."""
    top = chunks[: min(4, len(chunks))]
    q_tokens = set(re.findall(r"[a-z0-9]{3,}", query.lower()))
    evidence_hits = 0
    for chunk in top:
        if q_tokens & set(re.findall(r"[a-z0-9]{3,}", chunk.content.lower())):
            evidence_hits += 1
    if evidence_hits == 0 and top[0].score < max(settings.min_citation_score * 2, 0.12):
        return GenerationResult(
            answer=settings.no_answer_phrase,
            no_answer=True,
            model="extractive",
            citations=[],
        )

    sentences: list[str] = []
    for chunk in top:
        for sent in re.split(r"(?<=[.!?])\s+", chunk.content.strip()):
            cleaned = sent.strip()
            if len(cleaned) < 40:
                continue
            if q_tokens and not (q_tokens & set(re.findall(r"[a-z0-9]{3,}", cleaned.lower()))):
                continue
            cite = f"[{top.index(chunk) + 1}]"
            sentences.append(f"{cleaned} {cite}")
            if len(sentences) >= 4:
                break
        if len(sentences) >= 4:
            break

    if not sentences:
        # Fall back to first passage snippet with citation.
        snippet = top[0].content.strip()
        if len(snippet) > 420:
            snippet = snippet[:417].rstrip() + "..."
        answer = f"{snippet} [1]"
    else:
        answer = " ".join(sentences)

    preface = "Based on your company knowledge:\n\n"
    return GenerationResult(
        answer=preface + answer,
        no_answer=False,
        model="extractive",
        citations=top,
    )


def _generate_openai(
    settings: Settings,
    *,
    query: str,
    chunks: list[RetrievedChunk],
) -> GenerationResult:
    api_key = settings.effective_llm_api_key
    if not api_key:
        return _generate_extractive(settings, query=query, chunks=chunks)

    context = _build_context(chunks, settings.chat_max_context_chars)
    system = (
        "You are Vridhi, an AI business knowledge assistant. "
        "Answer ONLY using the provided company sources. "
        "If the sources are insufficient, reply exactly with: "
        f"{settings.no_answer_phrase} "
        "Cite sources inline like [1], [2] matching the source numbers. "
        "Do not invent facts, numbers, or documents."
    )
    user = f"Question: {query}\n\nSources:\n{context}"
    resp = httpx.post(
        f"{settings.llm_api_base.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        timeout=90.0,
    )
    resp.raise_for_status()
    answer = resp.json()["choices"][0]["message"]["content"].strip()
    no_answer = settings.no_answer_phrase.lower() in answer.lower() and len(answer) < len(
        settings.no_answer_phrase
    ) + 40
    if no_answer or answer.strip() == settings.no_answer_phrase:
        return GenerationResult(
            answer=settings.no_answer_phrase,
            no_answer=True,
            model=settings.llm_model,
            citations=[],
        )
    return GenerationResult(
        answer=answer,
        no_answer=False,
        model=settings.llm_model,
        citations=chunks[: min(6, len(chunks))],
    )
