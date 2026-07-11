"""Data-plane rate limiting — per-IP sliding window (C5, invariant 8).

Without rate limits any caller (even after step 1's auth is in place) can
flood /v1/chat in a tight loop, exhaust the Modal compute budget, and cause
denial-of-wallet / denial-of-service. This is finding C5 and breaks
invariant 8 (every run must have hard limits on time, tokens, tool calls,
and cost).

After the fix a sliding 60-second window per client IP caps requests to
GLC_DATA_PLANE_RPM (default 60). The 61st request in the same window gets
HTTP 429 with a Retry-After header.
"""

from __future__ import annotations

import pytest

import glc.security.api_auth as _auth


@pytest.fixture(autouse=True)
def _clear_rate_limit_state():
    """Wipe the in-process IP window dict before each test."""
    _auth._ip_windows.clear()
    yield
    _auth._ip_windows.clear()


@pytest.fixture
def enforced_client(monkeypatch):
    """Client with auth AND rate limiting active (GLC_DISABLE_API_AUTH cleared)."""
    monkeypatch.delenv("GLC_DISABLE_API_AUTH", raising=False)
    # Set a very low limit so tests run fast.
    monkeypatch.setattr(_auth, "_DEFAULT_RPM", 3)
    from fastapi.testclient import TestClient

    import glc.main as m

    with TestClient(m.app) as c:
        yield c


def _token():
    from glc.config import install_token_path

    return install_token_path().read_text().strip()


def test_requests_within_limit_succeed(enforced_client):
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(3):  # exactly at the cap
        r = enforced_client.get("/v1/status", headers=headers)
        assert r.status_code == 200


def test_request_over_limit_returns_429(enforced_client):
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(3):
        enforced_client.get("/v1/status", headers=headers)
    # 4th request must be rejected
    r = enforced_client.get("/v1/status", headers=headers)
    assert r.status_code == 429


def test_429_has_retry_after_header(enforced_client):
    token = _token()
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(3):
        enforced_client.get("/v1/status", headers=headers)
    r = enforced_client.get("/v1/status", headers=headers)
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}


def test_rate_limit_skipped_in_dev_mode(app_client):
    """GLC_DISABLE_API_AUTH=1 (dev mode) bypasses rate limiting too."""
    # app_client fixture sets GLC_DISABLE_API_AUTH=1 via conftest
    _auth._DEFAULT_RPM = 1  # absurdly low cap that would trip in prod
    try:
        for _ in range(5):
            r = app_client.get("/v1/status")
            assert r.status_code == 200
    finally:
        _auth._DEFAULT_RPM = int(__import__("os").getenv("GLC_DATA_PLANE_RPM", "60"))
