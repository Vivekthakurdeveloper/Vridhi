from app.services.document_kinds import classify_document


def test_classify_by_extension():
    assert classify_document("report.pdf", None) == "pdf"
    assert classify_document("notes.docx", None) == "docx"
    assert classify_document("old.doc", None) == "doc"
    assert classify_document("sheet.xlsx", None) == "xlsx"
    assert classify_document("old.xls", None) == "xls"
    assert classify_document("deck.pptx", None) == "pptx"
    assert classify_document("old.ppt", None) == "ppt"
    assert classify_document("notes.txt", None) == "text"
    assert classify_document("data.csv", None) == "text"


def test_classify_by_mime_when_extension_ambiguous():
    assert classify_document("file", "application/msword") == "doc"
    assert classify_document("file", "application/vnd.ms-excel") == "xls"
    assert classify_document("file", "application/vnd.ms-powerpoint") == "ppt"
    assert classify_document(
        "file",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ) == "docx"
    assert classify_document(
        "file",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ) == "xlsx"
    assert classify_document(
        "file",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ) == "pptx"


def test_classify_modern_extension_never_routes_to_legacy():
    # .docx must never be classified as "doc" even if something odd is passed as mime.
    assert classify_document("notes.docx", "application/msword") == "docx"


def test_classify_unsupported_returns_none():
    assert classify_document("photo.png", "image/png") is None
    assert classify_document("archive.zip", "application/zip") is None
