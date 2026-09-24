"""Open a ZIP in memory and return its supported inner files.

No size/count limits are enforced here (accepted scope cut for Piece 4A --
see the plan's "Scope cut for speed" section). The one thing that IS
enforced, non-negotiably, is path safety: an entry is never allowed to
resolve outside the archive (zip-slip). Nothing is ever written to disk.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO

from app.services.document_kinds import classify_document


@dataclass
class ZipEntry:
    path: str
    data: bytes
    kind: str


def _is_safe_path(path: str) -> bool:
    if path.startswith("/") or path.startswith("\\"):
        return False
    if len(path) > 1 and path[1] == ":":  # Windows drive letter, e.g. "C:"
        return False
    parts = path.replace("\\", "/").split("/")
    return ".." not in parts


def expand_zip(data: bytes) -> list[ZipEntry]:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
        bad_entry = zf.testzip()
        if bad_entry is not None:
            raise ValueError(f"corrupt zip entry: {bad_entry}")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid zip file: {exc}") from exc

    entries: list[ZipEntry] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        path = info.filename
        if "__MACOSX/" in path or path.endswith(".DS_Store"):
            continue
        if not _is_safe_path(path):
            continue
        kind = classify_document(path, None)
        if kind is None:
            continue
        entries.append(ZipEntry(path=path, data=zf.read(info), kind=kind))
    return entries
