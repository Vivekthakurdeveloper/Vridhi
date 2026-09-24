"""Only the DB-light helper is unit-tested here (matches tombstone/gmail_history
test style). Full sync-job behavior is covered by scripts/smoke-phase-j.sh
against the running mock stack, same convention as Drive/Gmail's worker code.

Lives in app/services/chat_threads.py (a pure module, no DB/network deps)
rather than worker/chat_sync.py, so this test doesn't need `worker` on
PYTHONPATH (see .github/workflows/ci.yml's C1 fix)."""

from app.services.chat_threads import member_grant_set_changed


def test_member_grant_set_changed_true_when_different():
    assert member_grant_set_changed(before={"a", "b"}, after={"a"})


def test_member_grant_set_changed_false_when_same():
    assert not member_grant_set_changed(before={"a", "b"}, after={"b", "a"})
