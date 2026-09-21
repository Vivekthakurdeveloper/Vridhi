"""Interpret Gmail History API records (Phase H).

``users.history.list`` returns change records since a checkpoint (historyId).
We only care which messages appeared and which went away (deleted, or moved to
Trash - which ``messages.list`` also hides). Records are applied in id order and
a later record overrides an earlier one for the same message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

TRASH = "TRASH"


@dataclass
class HistoryChanges:
    added: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)


def _message_id(item: dict[str, Any]) -> str:
    return str((item.get("message") or {}).get("id") or "")


def interpret_history(records: Iterable[dict[str, Any]]) -> HistoryChanges:
    added: set[str] = set()
    removed: set[str] = set()

    def gone(mid: str) -> None:
        if mid:
            removed.add(mid)
            added.discard(mid)

    def here(mid: str) -> None:
        if mid:
            added.add(mid)
            removed.discard(mid)

    for record in sorted(records, key=lambda r: int(r["id"])):
        for item in record.get("messagesAdded") or []:
            here(_message_id(item))
        for item in record.get("messagesDeleted") or []:
            gone(_message_id(item))
        for item in record.get("labelsAdded") or []:
            if TRASH in (item.get("labelIds") or []):
                gone(_message_id(item))
        for item in record.get("labelsRemoved") or []:
            if TRASH in (item.get("labelIds") or []):
                here(_message_id(item))
    return HistoryChanges(added=added, removed=removed)


def message_id_from_external_id(external_id: str) -> str:
    """Gmail documents use external_id ``"<message_id>:<attachment_id>"``."""
    return external_id.split(":", 1)[0]
