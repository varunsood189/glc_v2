"""Bearer-token authentication and rate limiting for the public data plane.

The data-plane routes (/v1/chat, /v1/vision, /v1/embed, /v1/transcribe,
/v1/speak and the read-only info endpoints) ran unauthenticated in glc_v1,
which was safe on localhost but becomes a public, abusable, cost-amplifying
surface once the gateway is deployed to a public Modal URL. This module
provides two FastAPI dependencies:

  require_api_token  — bearer-token check (step 1).
  check_rate_limit   — per-client-IP sliding-window rate limit (step 9).

Authentication can be disabled for local development by setting
GLC_DISABLE_API_AUTH=1 (never set this in a deployed environment).
Rate limiting can be tuned with GLC_DATA_PLANE_RPM (default: 60 req/min).
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import deque

from fastapi import Header, HTTPException, Request

from glc.config import get_or_create_install_token

_log = logging.getLogger(__name__)


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    if os.getenv("GLC_DISABLE_API_AUTH") == "1":
        return
    expected = get_or_create_install_token()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token (Authorization: Bearer <install_token>)")
    presented = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(403, "invalid token")


# ---------------------------------------------------------------------------
# Per-IP sliding-window rate limiter for the HTTP data plane (C5 / invariant 8)
# ---------------------------------------------------------------------------

_DEFAULT_RPM: int = int(os.getenv("GLC_DATA_PLANE_RPM", "60"))
_WINDOW_SECONDS: int = 60

_ip_windows: dict[str, deque[float]] = {}
_ratelimit_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    """Best-effort client IP, respecting X-Forwarded-For from trusted proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request) -> None:
    """Sliding-window rate limit: GLC_DATA_PLANE_RPM requests per 60 s per IP.

    Returns immediately if GLC_DISABLE_API_AUTH=1 (dev mode) or if the env
    var is not set and the limit is not breached. Raises HTTP 429 on breach.
    """
    if os.getenv("GLC_DISABLE_API_AUTH") == "1":
        return

    ip = _client_ip(request)
    rpm = _DEFAULT_RPM
    now = time.monotonic()
    horizon = now - _WINDOW_SECONDS

    with _ratelimit_lock:
        dq = _ip_windows.setdefault(ip, deque())
        # evict timestamps older than the window
        while dq and dq[0] < horizon:
            dq.popleft()
        if len(dq) >= rpm:
            _log.warning("rate limit exceeded for ip=%s count=%d rpm=%d", ip, len(dq), rpm)
            raise HTTPException(
                429,
                f"rate limit exceeded — maximum {rpm} requests per minute",
                headers={"Retry-After": str(_WINDOW_SECONDS)},
            )
        dq.append(now)
