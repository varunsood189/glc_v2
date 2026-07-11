"""Data-plane authentication (glc.security.api_auth).

Verifies that the /v1 data-plane routes reject anonymous callers once the
GLC_DISABLE_API_AUTH escape hatch is cleared, and accept the install token.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def enforced_client(monkeypatch, tmp_path):
    monkeypatch.delenv("GLC_DISABLE_API_AUTH", raising=False)
    from fastapi.testclient import TestClient

    import glc.main as m

    with TestClient(m.app) as c:
        yield c


def _token():
    from glc.config import install_token_path

    return install_token_path().read_text().strip()


def test_chat_requires_token(enforced_client):
    r = enforced_client.post("/v1/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_status_requires_token(enforced_client):
    assert enforced_client.get("/v1/status").status_code == 401


def test_wrong_token_is_forbidden(enforced_client):
    r = enforced_client.get("/v1/status", headers={"Authorization": "Bearer not-the-token"})
    assert r.status_code == 403


def test_valid_token_is_accepted(enforced_client):
    r = enforced_client.get("/v1/status", headers={"Authorization": f"Bearer {_token()}"})
    assert r.status_code == 200
