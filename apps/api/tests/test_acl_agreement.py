"""The one place that asserts Vridhi's three ACL implementations agree.

The same authorization policy is written three times in three languages:

  1. ``DocumentService.can_access``      -- imperative Python (app/services/documents.py)
  2. ``DocumentService.accessible_filter`` -- SQLAlchemy predicate (same file)
  3. ``OpenSearchRetriever.acl_filter``  -- OpenSearch query DSL (app/services/retrieval.py)

They agree today and they will drift (ARCHITECTURE_NOTES.md §8 item 3). The
tests below pin the shared shape of all three -- in particular that a
``selected`` document is reachable through a *group* grant in every one of them,
and that admins short-circuit in every one of them. They are deliberately pure:
``can_access`` is a pure function, the SQLAlchemy construct is compiled to SQL
text rather than executed, and ``acl_filter`` returns a plain dict -- so no
Postgres and no OpenSearch cluster is needed.
"""

from uuid import uuid4

from sqlalchemy.dialects import postgresql

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


def _retriever():
    from app.services.retrieval import OpenSearchRetriever

    # acl_filter touches no instance state, so skip __init__ (which would build a
    # live OpenSearch client).
    return OpenSearchRetriever.__new__(OpenSearchRetriever)


def _sql(clause) -> str:
    """Compiled SQL text for a SQLAlchemy ClauseElement (no DB connection)."""
    return str(clause.compile(dialect=postgresql.dialect()))


def _terms_fields(node) -> set[str]:
    """Every field name used in a term/terms leaf anywhere in an OpenSearch query."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("term", "terms") and isinstance(value, dict):
                found |= set(value.keys())
            found |= _terms_fields(value)
    elif isinstance(node, list):
        for item in node:
            found |= _terms_fields(item)
    return found


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


# --- the trio-agreement tests ------------------------------------------------


def test_all_three_acl_implementations_honour_group_grants():
    """can_access, accessible_filter and acl_filter must all reach a `selected`
    document through a group grant -- not just the imperative one."""
    svc = _service()
    tenant_id, user_id, group_id = uuid4(), uuid4(), uuid4()

    # 1. imperative
    assert (
        svc.can_access(
            _FakeDoc(DocumentVisibility.selected, uploaded_by_user_id=uuid4()),
            user_id=user_id,
            role=MemberRole.member,
            grant_user_ids=set(),
            grant_group_ids={group_id},
            user_group_ids={group_id},
        )
        is True
    )

    # 2. SQL: a `selected` document is reachable via EITHER a direct user grant
    #    or a group grant joined through group_memberships.
    sql = _sql(svc.accessible_filter(tenant_id, user_id, MemberRole.member))
    assert "document_grants" in sql
    assert "document_group_grants" in sql
    assert "group_memberships" in sql
    assert sql.count("EXISTS") == 2, sql
    # ...and the group branch is an alternative to (not a requirement on top of)
    # the direct-grant branch.
    assert "OR (EXISTS" in sql, sql

    # 3. OpenSearch DSL: same two alternatives, expressed as term/terms clauses.
    dsl = _retriever().acl_filter(
        tenant_id=tenant_id,
        user_id=user_id,
        role=MemberRole.member,
        user_group_ids={group_id},
    )
    fields = _terms_fields(dsl)
    assert "granted_user_ids" in fields
    assert "granted_group_ids" in fields
    assert {"terms": {"granted_group_ids": [str(group_id)]}} in _selected_should(dsl)


def test_acl_filter_omits_group_clause_when_user_has_no_groups():
    """No groups must mean no `granted_group_ids` clause at all -- an empty
    `terms` list would match nothing but still widen the query needlessly, and a
    present-but-empty clause is easy to mistake for 'group grants are checked'."""
    retriever = _retriever()
    for no_groups in (set(), None):
        dsl = retriever.acl_filter(
            tenant_id=uuid4(),
            user_id=uuid4(),
            role=MemberRole.member,
            user_group_ids=no_groups,
        )
        fields = _terms_fields(dsl)
        assert "granted_user_ids" in fields
        assert "granted_group_ids" not in fields, dsl


def test_all_three_acl_implementations_short_circuit_for_admins():
    """Admin+ bypasses visibility in all three, and none of them keeps a
    grant/visibility predicate around for admins."""
    svc = _service()
    tenant_id, user_id = uuid4(), uuid4()

    assert (
        svc.can_access(
            _FakeDoc(DocumentVisibility.private, uploaded_by_user_id=uuid4()),
            user_id=user_id,
            role=MemberRole.admin,
            grant_user_ids=set(),
            grant_group_ids=set(),
            user_group_ids=set(),
        )
        is True
    )

    sql = _sql(svc.accessible_filter(tenant_id, user_id, MemberRole.admin))
    assert "EXISTS" not in sql
    assert "visibility" not in sql
    assert "documents.tenant_id" in sql  # tenancy is never bypassed

    dsl = _retriever().acl_filter(
        tenant_id=tenant_id, user_id=user_id, role=MemberRole.admin, user_group_ids={uuid4()}
    )
    assert _terms_fields(dsl) == {"tenant_id"}


def _selected_should(dsl) -> list:
    """The `should` list guarding `visibility=selected` inside an acl_filter."""
    for clause in dsl["bool"]["must"]:
        for branch in (clause.get("bool") or {}).get("should") or []:
            inner = (branch.get("bool") or {}).get("must") or []
            if {"term": {"visibility": "selected"}} in inner:
                for sub in inner:
                    should = (sub.get("bool") or {}).get("should")
                    if should is not None:
                        return should
    raise AssertionError(f"no visibility=selected should-clause found in {dsl!r}")
