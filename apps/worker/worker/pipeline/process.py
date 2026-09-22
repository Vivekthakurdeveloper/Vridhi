from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Iterable, List

from app.services.document_kinds import classify_document

logger = logging.getLogger(__name__)


def parse_bytes(filename: str, mime_type: str | None, data: bytes) -> str:
    kind = classify_document(filename, mime_type)
    if kind == "pdf":
        return _parse_pdf(data)
    if kind == "docx":
        return _parse_docx(data)
    if kind == "xlsx":
        return _parse_xlsx(data)
    if kind == "pptx":
        return _parse_pptx(data)
    if kind == "doc":
        return _parse_legacy_office(data, "catdoc")
    if kind == "xls":
        return _parse_legacy_office(data, "xls2csv")
    if kind == "ppt":
        return _parse_legacy_office(data, "catppt")
    if kind == "text":
        return data.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported file type for parsing: {filename}")


def _parse_pdf(data: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    parts = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)


def _parse_docx(data: bytes) -> str:
    from io import BytesIO

    from docx import Document

    doc = Document(BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _parse_xlsx(data: bytes) -> str:
    from io import BytesIO

    from openpyxl import load_workbook

    wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for sheet in wb.worksheets:
        parts.append(f"# Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None and str(c).strip()]
            if cells:
                parts.append("\t".join(cells))
    return "\n".join(parts)


def _parse_pptx(data: bytes) -> str:
    from io import BytesIO

    from pptx import Presentation

    prs = Presentation(BytesIO(data))
    parts: list[str] = []
    for idx, slide in enumerate(prs.slides, start=1):
        texts = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text and shape.text.strip():
                texts.append(shape.text.strip())
        if texts:
            parts.append(f"# Slide {idx}\n" + "\n".join(texts))
    return "\n\n".join(parts)


def _parse_legacy_office(data: bytes, tool: str) -> str:
    """Read text out of an old-format .doc/.xls/.ppt file via the `catdoc`
    family of tools (installed in the worker image -- see apps/worker/Dockerfile).
    No timeout handling beyond the caller's existing per-file try/except
    (accepted scope cut for Piece 4A)."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".bin") as tmp:
        tmp.write(data)
        tmp.flush()
        result = subprocess.run(
            [tool, tmp.name], capture_output=True, timeout=60
        )
    if result.returncode != 0:
        raise ValueError(f"{tool} failed to read this file")
    return result.stdout.decode("utf-8", errors="replace")


def chunk_text(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
    min_chars: int,
) -> List[str]:
    cleaned = re.sub(r"\r\n?", "\n", text).strip()
    if not cleaned:
        return []
    if len(cleaned) <= chunk_size:
        return [cleaned]

    chunks: list[str] = []
    start = 0
    length = len(cleaned)
    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            # Prefer breaking on whitespace near the end.
            window = cleaned[start:end]
            break_at = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
            if break_at > chunk_size * 0.5:
                end = start + break_at
        piece = cleaned[start:end].strip()
        if len(piece) >= min_chars or not chunks:
            if piece:
                chunks.append(piece)
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def hash_embed(texts: Iterable[str], dimensions: int) -> list[list[float]]:
    """Deterministic local embedding (no external API). Configurable via EMBEDDING_PROVIDER=hash."""
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
        # L2 normalize
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
    import httpx

    if not api_key:
        raise RuntimeError("EMBEDDING_API_KEY is required when EMBEDDING_PROVIDER=openai")
    payload = {"model": model, "input": texts}
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
