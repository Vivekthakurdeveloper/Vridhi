from app.config import Settings


def test_chat_settings_defaults(monkeypatch):
    monkeypatch.delenv("GOOGLE_CHAT_ENABLED", raising=False)
    monkeypatch.delenv("GOOGLE_CHAT_MODE", raising=False)
    settings = Settings()
    assert settings.google_chat_enabled is False
    assert settings.google_chat_mode == "mock"
    assert settings.google_chat_ready is False  # disabled by default, like Gmail


def test_chat_ready_when_enabled_mock(monkeypatch):
    monkeypatch.setenv("GOOGLE_CHAT_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CHAT_MODE", "mock")
    settings = Settings()
    assert settings.google_chat_ready is True


def test_chat_allowed_mime_set_parses_csv():
    settings = Settings()
    assert "application/pdf" in settings.chat_allowed_mime_set
    assert isinstance(settings.chat_allowed_mime_set, set)


def test_chat_scope_list_splits_on_whitespace():
    settings = Settings(google_chat_scopes="scope.one scope.two")
    assert settings.chat_scope_list == ["scope.one", "scope.two"]
