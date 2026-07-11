"""Docs/OpenAPI suppression in production mode (GLC_ENV=prod).

/docs, /redoc, and /openapi.json expose the full route inventory, provider
order, model names, rate limits, and request/response shapes. Even with the
data-plane auth from step 1, FastAPI serves these schema routes before any
dependency is evaluated — so they leak to anonymous callers. Setting
GLC_ENV=prod suppresses all three (finding A2, invariant 8).

In dev (GLC_ENV unset) they must still be served so local development works.
"""

from __future__ import annotations

import importlib

import pytest


def _make_client(monkeypatch, glc_env: str | None):
    """Boot a fresh FastAPI app with the given GLC_ENV value."""
    if glc_env is None:
        monkeypatch.delenv("GLC_ENV", raising=False)
    else:
        monkeypatch.setenv("GLC_ENV", glc_env)

    # Re-import glc.main so the module-level _prod flag is re-evaluated.
    import glc.main as m
    importlib.reload(m)

    from fastapi.testclient import TestClient
    with TestClient(m.app) as c:
        yield c


@pytest.fixture
def prod_client(monkeypatch):
    yield from _make_client(monkeypatch, "prod")


@pytest.fixture
def dev_client(monkeypatch):
    yield from _make_client(monkeypatch, None)


def test_docs_hidden_in_prod(prod_client):
    assert prod_client.get("/docs").status_code == 404


def test_redoc_hidden_in_prod(prod_client):
    assert prod_client.get("/redoc").status_code == 404


def test_openapi_hidden_in_prod(prod_client):
    assert prod_client.get("/openapi.json").status_code == 404


def test_docs_served_in_dev(dev_client):
    assert dev_client.get("/docs").status_code == 200


def test_openapi_served_in_dev(dev_client):
    assert dev_client.get("/openapi.json").status_code == 200
