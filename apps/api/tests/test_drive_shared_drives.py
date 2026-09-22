from app.services.drive import MOCK_FOLDERS, get_mock_files


def test_mock_shared_drive_folder_present():
    ids = {f["id"] for f in MOCK_FOLDERS}
    assert "folder-shared-legal" in ids
    shared = next(f for f in MOCK_FOLDERS if f["id"] == "folder-shared-legal")
    assert shared.get("drive_name") == "Legal (Shared Drive)"


def test_mock_shared_drive_has_a_file():
    files = get_mock_files("folder-shared-legal")
    assert len(files) == 1
    assert files[0]["name"] == "NDA Template.txt"
