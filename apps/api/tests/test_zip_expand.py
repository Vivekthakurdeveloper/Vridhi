import io
import zipfile

import pytest

from app.services.zip_expand import expand_zip


def _make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in entries.items():
            zf.writestr(path, data)
    return buf.getvalue()


def test_expand_zip_returns_supported_entries():
    data = _make_zip({
        "Leave.pdf": b"%PDF-1.4 fake pdf bytes",
        "notes.txt": b"hello world",
        "photo.png": b"not supported",
    })
    entries = expand_zip(data)
    paths = {e.path for e in entries}
    assert paths == {"Leave.pdf", "notes.txt"}
    leave = next(e for e in entries if e.path == "Leave.pdf")
    assert leave.kind == "pdf"
    assert leave.data == b"%PDF-1.4 fake pdf bytes"


def test_expand_zip_rejects_parent_traversal():
    data = _make_zip({"../../etc/passwd": b"evil", "ok.txt": b"fine"})
    entries = expand_zip(data)
    paths = {e.path for e in entries}
    assert paths == {"ok.txt"}


def test_expand_zip_rejects_absolute_paths():
    data = _make_zip({"/etc/passwd": b"evil", "ok.txt": b"fine"})
    entries = expand_zip(data)
    paths = {e.path for e in entries}
    assert paths == {"ok.txt"}


def test_expand_zip_skips_directory_entries():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("folder/", b"")
        zf.writestr("folder/file.txt", b"hi")
    entries = expand_zip(buf.getvalue())
    assert [e.path for e in entries] == ["folder/file.txt"]


def test_expand_zip_bad_bytes_raises():
    with pytest.raises(ValueError):
        expand_zip(b"not a zip at all")
