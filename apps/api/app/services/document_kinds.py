"""One shared rule for "what kind of document is this", used by the file
reader (worker/pipeline/process.py), the Drive ZIP expander
(services/zip_expand.py), and Drive's ZIP-entry filter -- so all three can
never disagree about what is supported.
"""
from __future__ import annotations

_EXTENSION_KINDS: dict[str, str] = {
    "pdf": "pdf",
    "docx": "docx",
    "doc": "doc",
    "xlsx": "xlsx",
    "xls": "xls",
    "pptx": "pptx",
    "ppt": "ppt",
    "txt": "text",
    "csv": "text",
}

_MIME_KINDS: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-powerpoint": "ppt",
}


def classify_document(filename: str, mime: str | None) -> str | None:
    """Return a document kind ("pdf", "docx", "doc", "xlsx", "xls", "pptx",
    "ppt", "text") or None if unsupported. Extension wins when it names a
    known kind (so a .docx is never routed to the legacy .doc reader even if
    a caller passes a stale/wrong mime); mime is the fallback for extensionless
    names (e.g. Gmail attachments, Drive exports)."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _EXTENSION_KINDS:
        return _EXTENSION_KINDS[ext]
    mime_norm = (mime or "").lower().split(";")[0].strip()
    if mime_norm in _MIME_KINDS:
        return _MIME_KINDS[mime_norm]
    if mime_norm.startswith("text/"):
        return "text"
    return None
