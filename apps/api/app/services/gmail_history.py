"""Interpret Gmail History API records (Phase H).

``users.history.list`` returns change records since a checkpoint (historyId).
We only care which messages appeared and which went away (deleted, or moved to
Trash - which ``messages.list`` also hides). Records are applied in id order and
a later record overrides an earlier one for the same message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

TRASH = "TRASH"
# messages.list leaves these out, so the incremental path and the full pass
# must agree that a message carrying either label is not part of the mailbox.
EXCLUDED_LABELS = {"SPAM", TRASH}


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
            if EXCLUDED_LABELS.intersection(item.get("labelIds") or []):
                gone(_message_id(item))
        for item in record.get("labelsRemoved") or []:
            if EXCLUDED_LABELS.intersection(item.get("labelIds") or []):
                here(_message_id(item))
    return HistoryChanges(added=added, removed=removed)


def has_excluded_label(message: Optional[dict[str, Any]]) -> bool:
    """True if the message sits in Spam or Trash (a fetched Gmail message)."""
    if message is None:
        return False
    return bool(EXCLUDED_LABELS.intersection(message.get("labelIds") or []))


def is_gone(message: Optional[dict[str, Any]]) -> bool:
    """A message we looked up is gone if Gmail no longer has it, or it is in Spam/Trash."""
    return message is None or has_excluded_label(message)


def partition_added(
    added_ids: Iterable[str], fetched: dict[str, Optional[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], set[str]]:
    """Split History's "added" ids using the messages as actually fetched.

    History alone can be wrong (e.g. a Spam -> Trash move is reported as
    labelsAdded TRASH + labelsRemoved SPAM, which reads as "added"), so the
    fetched state is authoritative: a message that is gone, or now in Spam/Trash,
    is returned as removed instead of being ingested. Returns
    (messages to ingest in id order, ids to treat as removed).
    """
    to_ingest: list[dict[str, Any]] = []
    removed: set[str] = set()
    for mid in sorted(added_ids):
        message = fetched.get(mid)
        if is_gone(message):
            removed.add(mid)
        else:
            to_ingest.append(message)  # type: ignore[arg-type]
    return to_ingest, removed


def message_id_from_external_id(external_id: str) -> str:
    """Gmail documents use external_id ``"<message_id>:<attachment_id>"``."""
    return external_id.split(":", 1)[0]
