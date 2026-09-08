from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def test_healthz():
    get_settings.cache_clear()
    client = TestClient(create_app())
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_register_validation_error_shape():
    get_settings.cache_clear()
    client = TestClient(create_app())
    res = client.post("/v1/auth/register", json={"email": "bad"})
    assert res.status_code == 400
    body = res.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "request_id" in body["error"]
