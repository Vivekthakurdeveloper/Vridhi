"""Shared helpers for calling Google APIs from worker jobs.

Google reports per-minute quota exhaustion as HTTP 403 with reason
"rateLimitExceeded" in the body -- not the more common 429. Confirmed
live against a real Gmail account: a mailbox with enough matching
messages blows through the quota mid-sync, and without this retry the
whole job dies on whichever call happens to trip it, even though the
very next attempt at that same call succeeds once the per-minute window
rolls over. Both Gmail sync and Groups sync (Admin SDK Directory API)
hit the same class of limit, so this lives in one shared place rather
than being reimplemented per connector.
"""

from __future__ import annotations

from typing import Any

_RATE_LIMIT_MAX_RETRIES = 2
_RATE_LIMIT_BACKOFF_SECONDS = (2, 4)


def google_get_with_retry(url: str, *, params: dict[str, Any] | None, headers: dict[str, str], timeout: float):
    """httpx.get with one short backoff-and-retry on a transient rate limit.

    Any other error status (auth failure, real permission denial, 404, etc.)
    is raised immediately -- retrying those would just waste time on a
    failure that will never resolve itself.
    """
    import time

    import httpx

    for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
        resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code == 403 and "rateLimitExceeded" in resp.text and attempt < _RATE_LIMIT_MAX_RETRIES:
            time.sleep(_RATE_LIMIT_BACKOFF_SECONDS[attempt])
            continue
        resp.raise_for_status()
        return resp
    return resp  # unreachable, satisfies type checkers
