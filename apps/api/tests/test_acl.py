from uuid import uuid4

from app.models import Document
from app.security import DocumentStatus, DocumentVisibility, MemberRole
from app.services.documents import DocumentService
from app.services.retrieval import OpenSearchRetriever


class _FakeSettings:
    pass


def _doc(**kwargs) -> Document:
    defaults = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        title="t",
        source="file_upload",
        visibility=DocumentVisibility.private,
        status=DocumentStatus.ready,
        uploaded_by_user_id=uuid4(),
        deleted_at=None,
    )
    defaults.update(kwargs)
    return Document(**defaults)


def test_can_access_fail_closed_unknown_visibility():
    svc = DocumentService(db=None, settings=_FakeSettings(), storage=None, queue=None)  # type: ignore[arg-type]
    user = uuid4()
    doc = _doc(visibility="public")  # type: ignore[arg-type]
    # Invalid/unknown visibility must deny
    doc.visibility = "public"  # type: ignore[assignment]
    assert svc.can_access(doc, user_id=user, role=MemberRole.member) is False


def test_can_access_private_only_uploader_or_admin():
    svc = DocumentService(db=None, settings=_FakeSettings(), storage=None, queue=None)  # type: ignore[arg-type]
    owner = uuid4()
    other = uuid4()
    doc = _doc(visibility=DocumentVisibility.private, uploaded_by_user_id=owner)
    assert svc.can_access(doc, user_id=owner, role=MemberRole.member) is True
    assert svc.can_access(doc, user_id=other, role=MemberRole.member) is False
    assert svc.can_access(doc, user_id=other, role=MemberRole.admin) is True


def test_can_access_org_visible_to_members():
    svc = DocumentService(db=None, settings=_FakeSettings(), storage=None, queue=None)  # type: ignore[arg-type]
    doc = _doc(visibility=DocumentVisibility.org, uploaded_by_user_id=uuid4())
    assert svc.can_access(doc, user_id=uuid4(), role=MemberRole.member) is True


def test_can_access_selected_requires_grant():
    svc = DocumentService(db=None, settings=_FakeSettings(), storage=None, queue=None)  # type: ignore[arg-type]
    allowed = uuid4()
    denied = uuid4()
    doc = _doc(visibility=DocumentVisibility.selected, uploaded_by_user_id=uuid4())
    assert svc.can_access(doc, user_id=allowed, role=MemberRole.member, grant_user_ids={allowed}) is True
    assert svc.can_access(doc, user_id=denied, role=MemberRole.member, grant_user_ids={allowed}) is False


def test_can_access_deleted_denied():
    svc = DocumentService(db=None, settings=_FakeSettings(), storage=None, queue=None)  # type: ignore[arg-type]
    owner = uuid4()
    doc = _doc(status=DocumentStatus.deleted, uploaded_by_user_id=owner)
    assert svc.can_access(doc, user_id=owner, role=MemberRole.owner) is False


def test_acl_filter_includes_tenant_and_denies_open_access():
    retriever = OpenSearchRetriever.__new__(OpenSearchRetriever)
    tenant = uuid4()
    user = uuid4()
    filt = OpenSearchRetriever.acl_filter(
        retriever, tenant_id=tenant, user_id=user, role=MemberRole.member
    )
    assert filt["bool"]["must"][0] == {"term": {"tenant_id": str(tenant)}}
    # Member filter must not be empty open bool
    assert len(filt["bool"]["must"]) >= 2
