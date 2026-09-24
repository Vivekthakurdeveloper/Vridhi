"""Pure Google Chat thread logic (Piece 4B). No DB, no network — the worker
and service layers call these and do the I/O. One Document per thread
(`spaces/<S>/threads/<T>`, the Chat API's own resource name, already unique
across spaces so it is used directly as the external_id)."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _parse_rfc3339(value: str) -> datetime | None:
    """Best-effort RFC3339 parse. Returns None (never raises) for anything
    that doesn't parse, so callers can fall back gracefully."""
    if not value:
        return None
    v = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def group_messages_by_thread(messages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for m in messages:
        thread_name = str((m.get("thread") or {}).get("name") or "")
        if not thread_name:
            continue
        grouped.setdefault(thread_name, []).append(m)
    for thread_name in grouped:
        grouped[thread_name] = sorted(grouped[thread_name], key=lambda m: str(m.get("createTime") or ""))
    return grouped


def filter_messages_since(messages: list[dict[str, Any]], cursor: str) -> list[dict[str, Any]]:
    """Messages strictly newer than ``cursor``.

    Compares parsed ``datetime`` values, not the raw RFC3339 strings: a
    lexicographic comparison misorders whenever fractional-second presence
    differs between two timestamps (e.g. "...:00.5Z" sorts *before*
    "...:00Z" as a string, since '.' < 'Z', despite being chronologically
    later). Falls back to the raw string comparison only if either value
    fails to parse, so a malformed timestamp still degrades rather than
    raising.
    """
    if not cursor:
        return messages
    cursor_dt = _parse_rfc3339(cursor)
    if cursor_dt is None:
        return [m for m in messages if str(m.get("createTime") or "") > cursor]
    kept = []
    for m in messages:
        create_time = str(m.get("createTime") or "")
        dt = _parse_rfc3339(create_time)
        is_newer = dt > cursor_dt if dt is not None else create_time > cursor
        if is_newer:
            kept.append(m)
    return kept


def newest_create_time(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return ""
    return max(str(m.get("createTime") or "") for m in messages)


def format_thread_transcript(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in sorted(messages, key=lambda m: str(m.get("createTime") or "")):
        sender = str((m.get("sender") or {}).get("displayName") or "Unknown")
        when = str(m.get("createTime") or "")
        text = str(m.get("text") or "")
        lines.append(f"{sender} — {when}: {text}")
    return "\n".join(lines)


def thread_title(space_display_name: str, messages: list[dict[str, Any]]) -> str:
    ordered = sorted(messages, key=lambda m: str(m.get("createTime") or ""))
    snippet = str((ordered[0].get("text") if ordered else "") or "").strip()
    if len(snippet) > 80:
        snippet = snippet[:80]
    return f"{space_display_name} — {snippet}" if snippet else space_display_name


def member_grant_set_changed(*, before: set, after: set) -> bool:
    """True when a thread's resolved space-member grant set differs from what
    was previously stored, i.e. whether a re-ingest is needed purely to
    refresh the denormalised OpenSearch ACL fields."""
    return before != after
