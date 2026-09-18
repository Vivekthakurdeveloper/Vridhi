from uuid import uuid4

from app.security import DocumentStatus, DocumentVisibility, MemberRole


class _FakeDoc:
    def __init__(self, visibility, uploaded_by_user_id, status=DocumentStatus.ready, deleted_at=None):
        self.visibility = visibility
        self.uploaded_by_user_id = uploaded_by_user_id
        self.status = status
        self.deleted_at = deleted_at


def _service():
    from app.services.documents import DocumentService

    return DocumentService.__new__(DocumentService)  # no __init__ needed for the pure branch


def test_group_member_can_access_selected_doc_via_group_grant():
    svc = _service()
    finance_group_id = uuid4()
    doc = _FakeDoc(DocumentVisibility.selected, uploaded_by_user_id=uuid4())
    result = svc.can_access(
        doc,
        user_id=uuid4(),
        role=MemberRole.member,
        grant_user_ids=set(),
        grant_group_ids={finance_group_id},
        user_group_ids={finance_group_id},
    )
    assert result is True


def test_non_member_cannot_access_via_unrelated_group():
    svc = _service()
    doc = _FakeDoc(DocumentVisibility.selected, uploaded_by_user_id=uuid4())
    result = svc.can_access(
        doc,
        user_id=uuid4(),
        role=MemberRole.member,
        grant_user_ids=set(),
        grant_group_ids={uuid4()},
        user_group_ids={uuid4()},  # a different, unrelated group
    )
    assert result is False


def test_direct_user_grant_still_works_alongside_group_grants():
    svc = _service()
    uid = uuid4()
    doc = _FakeDoc(DocumentVisibility.selected, uploaded_by_user_id=uuid4())
    result = svc.can_access(
        doc,
        user_id=uid,
        role=MemberRole.member,
        grant_user_ids={uid},
        grant_group_ids=set(),
        user_group_ids=set(),
    )
    assert result is True
