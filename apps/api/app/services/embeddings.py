from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable

import httpx

from app.config import Settings


def hash_embed(texts: Iterable[str], dimensions: int) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        vec = [0.0] * dimensions
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        if not tokens:
            vectors.append(vec)
            continue
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "big") % dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vectors.append([v / norm for v in vec])
    return vectors


def openai_embed(
    texts: list[str],
    *,
    api_key: str,
    api_base: str,
    model: str,
    dimensions: int,
) -> list[list[float]]:
    if not api_key:
        raise RuntimeError("EMBEDDING_API_KEY is required when EMBEDDING_PROVIDER=openai")
    payload: dict = {"model": model, "input": texts}
    if dimensions:
        payload["dimensions"] = dimensions
    resp = httpx.post(
        f"{api_base.rstrip('/')}/embeddings",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    data.sort(key=lambda row: row["index"])
    return [row["embedding"] for row in data]


def embed_query(settings: Settings, text: str) -> list[float]:
    provider = settings.embedding_provider.lower().strip()
    if provider == "openai":
        return openai_embed(
            [text],
            api_key=settings.embedding_api_key,
            api_base=settings.embedding_api_base,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )[0]
    return hash_embed([text], settings.embedding_dimensions)[0]
