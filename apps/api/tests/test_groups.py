from datetime import timedelta

from app.security import utcnow
from app.services.groups import _needs_refresh


def test_needs_refresh_when_never_synced():
    assert _needs_refresh(None) is True


def test_needs_refresh_when_stale():
    stale = utcnow() - timedelta(minutes=20)
    assert _needs_refresh(stale) is True


def test_no_refresh_when_fresh():
    fresh = utcnow() - timedelta(minutes=5)
    assert _needs_refresh(fresh) is False


def test_no_refresh_at_exactly_fifteen_minutes_boundary():
    exactly = utcnow() - timedelta(minutes=15)
    # Right at the boundary should not force a refresh (only strictly older does)
    assert _needs_refresh(exactly) is False
