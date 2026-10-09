"""
The rate limiter: counts correctly, keys correctly, and leaves room for
ordinary use of the dashboard.
"""
from __future__ import annotations

from app.security.rate_limit import TooManyRequests, Window


def test_a_key_is_allowed_up_to_the_limit():
    window = Window(limit=3, seconds=60.0)
    window.hit("a")
    window.hit("a")
    window.hit("a")
    try:
        window.hit("a")
        assert False, "a fourth hit should have been refused"
    except TooManyRequests:
        pass


def test_different_keys_do_not_share_a_budget():
    window = Window(limit=1, seconds=60.0)
    window.hit("tenant-a")
    window.hit("tenant-b")  # a different key's turn, not tenant-a's second hit


def test_a_hit_outside_the_window_is_forgotten():
    import time

    window = Window(limit=1, seconds=0.05)
    window.hit("a")
    time.sleep(0.1)  # well past the window, so the first hit is forgotten
    window.hit("a")


def test_starting_a_campaign_too_often_is_refused(client, tenant_factory, auth):
    """The route-level limit, exercised through the real HTTP layer."""
    tenant, key = tenant_factory("910")
    headers = auth(key)

    responses = []
    for _ in range(21):
        body = {
            "name": "Reminder calls",
            "channel": "PHONE",
            "numbers": "+923001234567",
        }
        created = client.post("/api/campaigns", json=body, headers=headers).json()
        responses.append(client.post(f"/api/campaigns/{created['id']}/start", headers=headers))

    statuses = [r.status_code for r in responses]
    assert 429 in statuses, "the 21st call in a minute should have been refused"
    assert statuses.count(429) == 1, "only the one that crossed the limit should be refused"
