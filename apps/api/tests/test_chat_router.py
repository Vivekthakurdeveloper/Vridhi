"""End-to-end against the FastAPI test client, mock mode. Mirrors the shape
of the existing gmail router (there is no prior gmail router test file in
this repo to copy from -- see conftest.py's docstring for how the
`authed_client`/`authed_admin_client` fixtures were built instead). Key
behaviors asserted here:
"""


def test_get_chat_connection_not_connected_returns_available(authed_client):
    resp = authed_client.get("/v1/connections/google_chat")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False


def test_chat_oauth_callback_then_list_spaces_returns_mock_fixture(authed_admin_client):
    start = authed_admin_client.get("/v1/connections/google_chat/oauth/start", follow_redirects=False)
    assert start.status_code == 302

    # chat_oauth_start sets CHAT_OAUTH_COOKIE ("vridhi_chat_oauth_state") to a
    # freshly generated state and TestClient persists that cookie in its jar
    # across requests on the same client instance. The callback compares the
    # incoming "state" query param against that cookie (see chat.py's
    # `expected != state` guard, which is unconditional -- not bypassed in
    # mock mode), so the callback needs the *real* state value, not a
    # placeholder, or it 401s instead of redirecting.
    state = authed_admin_client.cookies.get("vridhi_chat_oauth_state")
    assert state

    callback = authed_admin_client.get(
        "/v1/connections/google_chat/oauth/callback",
        params={"code": "mock", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert "chat=connected" in callback.headers["location"]

    spaces = authed_admin_client.get("/v1/connections/google_chat/spaces")
    assert spaces.status_code == 200
    ids = {s["id"] for s in spaces.json()["spaces"]}
    assert "spaces/mockspace-finance" in ids


def test_connectors_list_includes_google_chat(authed_client):
    resp = authed_client.get("/v1/connectors")
    assert resp.status_code == 200
    ids = {c["id"] for c in resp.json()["connectors"]}
    assert "google_chat" in ids
