"""Bearer-token authentication for the public data plane.

The data-plane routes (/v1/chat, /v1/vision, /v1/embed, /v1/transcribe,
/v1/speak and the read-only info endpoints) ran unauthenticated in glc_v1,
which was safe on localhost but becomes a public, abusable, cost-amplifying
surface once the gateway is deployed to a public Modal URL. This dependency
requires the same per-installation token the control plane already trusts.

Authentication can be disabled for local development by setting
GLC_DISABLE_API_AUTH=1 (never set this in a deployed environment).
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException

from glc.config import get_or_create_install_token


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    if os.getenv("GLC_DISABLE_API_AUTH") == "1":
        return
    expected = get_or_create_install_token()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token (Authorization: Bearer <install_token>)")
    presented = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(403, "invalid token")
