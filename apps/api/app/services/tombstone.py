"""Soft-delete a document and remove it from search (Phase H).

The single place that hides a document: used by Drive/Gmail deletion
propagation and by Vridhi's own Delete button. The record is kept
(``status=deleted`` + ``deleted_at`` + ``deleted_reason``) and an audit event is
written; the document's chunks are removed from the search index because
retrieval treats that index as the authorization authority (see
ARCHITECTURE_NOTES.md), so hiding the Postgres row alone would leave the text
answerable.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Protocol
from uuid import uuid4

from app.models import AuditEvent
from app.security import DocumentStatus, utcnow

logger = logging.getLogger(__name__)

REASON_MANUAL = "manual"
REASON_SOURCE_REMOVED = "source_removed"


class SearchCleanup(Protocol):
    def delete_by_document(self, tenant_id: str, document_id: str) -> None: ...


class NullSearchCleanup:
    """Used when the search backend is disabled (nothing is indexed)."""

    def delete_by_document(self, tenant_id: str, document_id: str) -> None:
        return None


def tombstone_document(
    db: Any,
    search: SearchCleanup,
    doc: Any,
    *,
    reason: str,
    actor_user_id: Any = None,
    request_id: str | None = None,
) -> None:
    """Hide ``doc``. Does not commit - the caller owns the transaction.

    The index is cleaned first: if that raises, the document is left fully
    visible and consistent (and retried on the next sync) rather than being
    marked deleted while its text stays searchable.
    """
    search.delete_by_document(str(doc.tenant_id), str(doc.id))
    doc.status = DocumentStatus.deleted
    doc.deleted_at = utcnow()
    doc.deleted_reason = reason
    db.add(
        AuditEvent(
            id=uuid4(),
            tenant_id=doc.tenant_id,
            user_id=actor_user_id,
            action="document.deleted",
            metadata_={"document_id": str(doc.id), "reason": reason},
            request_id=request_id,
        )
    )


def tombstone_many(
    db: Any,
    search: SearchCleanup,
    docs: Iterable[Any],
    *,
    reason: str,
    log_operation: str,
) -> int:
    """Tombstone each document in its own transaction; one failure never blocks
    the others. Returns how many were hidden."""
    count = 0
    for doc in docs:
        doc_id = str(doc.id)  # read now: the rollback below expires the row
        try:
            tombstone_document(db, search, doc, reason=reason)
            db.commit()
            count += 1
        except Exception as exc:
            db.rollback()
            # Type only, no traceback or message: search errors can echo request bodies.
            logger.warning(
                "tombstone.failed document_id=%s error=%s",
                doc_id,
                type(exc).__name__,
                extra={"operation": log_operation},
            )
    return count


def missing_external_ids(stored: Iterable[str], listed: Iterable[str]) -> set[str]:
    """Ids we have stored that the source no longer lists."""
    return set(stored) - set(listed)


def base_external_id(external_id: str) -> str:
    """The source id a stored external_id belongs to, stripping a ZIP inner
    path if present ("<zip_id>::<inner path>" -> "<zip_id>"). Used by Drive's
    deletion pass so a live ZIP's still-present inner files are never mistaken
    for something the source stopped listing (the listing only ever returns
    the ZIP's own id, never its inner paths)."""
    return external_id.split("::", 1)[0]
