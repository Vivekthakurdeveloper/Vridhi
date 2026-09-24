from app.services.retrieval import OpenSearchRetriever


class _FakeClient:
    def __init__(self):
        self.calls = []

    def delete_by_query(self, **kwargs):
        self.calls.append(kwargs)


def test_delete_by_document_targets_only_that_tenant_and_document():
    retriever = OpenSearchRetriever.__new__(OpenSearchRetriever)
    retriever.index = "vridhi-chunks"
    retriever.client = _FakeClient()

    retriever.delete_by_document("tenant-1", "doc-1")

    (call,) = retriever.client.calls
    assert call["index"] == "vridhi-chunks"
    must = call["body"]["query"]["bool"]["must"]
    assert {"term": {"tenant_id": "tenant-1"}} in must
    assert {"term": {"document_id": "doc-1"}} in must
    assert call["refresh"] is True
    assert call["ignore_unavailable"] is True
