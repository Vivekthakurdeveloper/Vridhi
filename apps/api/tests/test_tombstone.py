from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import AuditEvent
from app.security import DocumentStatus
from app.services.tombstone import (
    REASON_MANUAL,
    REASON_SOURCE_REMOVED,
    NullSearchCleanup,
    missing_external_ids,
    tombstone_document,
    tombstone_many,
)


class _FakeDB:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeSearch:
    def __init__(self, fail_for=None):
        self.calls = []
        self.fail_for = fail_for

    def delete_by_document(self, tenant_id, document_id):
        if document_id == self.fail_for:
            raise RuntimeError("opensearch down")
        self.calls.append((tenant_id, document_id))


def _doc():
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        status=DocumentStatus.ready,
        deleted_at=None,
        deleted_reason=None,
    )


def test_tombstone_hides_document_removes_chunks_and_audits():
    db, search, doc = _FakeDB(), _FakeSearch(), _doc()
    actor = uuid4()
    tombstone_document(db, search, doc, reason=REASON_MANUAL, actor_user_id=actor, request_id="r1")
    assert doc.status == DocumentStatus.deleted
    assert doc.deleted_at is not None
    assert doc.deleted_reason == "manual"
    assert search.calls == [(str(doc.tenant_id), str(doc.id))]
    (event,) = db.added
    assert isinstance(event, AuditEvent)
    assert event.action == "document.deleted"
    assert event.user_id == actor
    assert event.request_id == "r1"
    assert event.metadata_ == {"document_id": str(doc.id), "reason": "manual"}
    assert db.commits == 0  # the caller owns the transaction


def test_index_failure_leaves_document_untouched():
    db, doc = _FakeDB(), _doc()
    search = _FakeSearch(fail_for=str(doc.id))
    with pytest.raises(RuntimeError):
        tombstone_document(db, search, doc, reason=REASON_SOURCE_REMOVED)
    assert doc.status == DocumentStatus.ready
    assert doc.deleted_at is None
    assert doc.deleted_reason is None
    assert db.added == []


def test_tombstone_many_counts_successes_and_isolates_failures():
    db = _FakeDB()
    good1, bad, good2 = _doc(), _doc(), _doc()
    search = _FakeSearch(fail_for=str(bad.id))
    n = tombstone_many(db, search, [good1, bad, good2], reason=REASON_SOURCE_REMOVED, log_operation="test")
    assert n == 2
    assert db.commits == 2
    assert db.rollbacks == 1
    assert good1.deleted_at is not None and good2.deleted_at is not None
    assert bad.deleted_at is None


def test_missing_external_ids_is_a_set_difference():
    assert missing_external_ids(["a", "b", "c"], ["b"]) == {"a", "c"}
    assert missing_external_ids([], ["x"]) == set()
    assert missing_external_ids(["a"], ["a"]) == set()


def test_null_search_cleanup_does_nothing():
    assert NullSearchCleanup().delete_by_document("t", "d") is None
