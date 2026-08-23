"""
API authentication — spec section 12: "do not expose this
unauthenticated." A single shared bearer-token check, applied per-router
via `APIRouter(dependencies=[Depends(require_api_key)])` to every route
except `/health` and `/ready` (those are defined directly on the app in
main.py, outside any authenticated router — k8s liveness/readiness probes
hit them unauthenticated by convention, and they reveal nothing
sensitive).

Uses FastAPI's `HTTPBearer` security scheme (auto_error=False, so we keep
control of the exact status code/message) rather than a plain `Header`
parameter — this registers a real OpenAPI security scheme, which is what
makes Swagger UI show the "Authorize" padlock. A raw `authorization`
Header parameter looks the same in the docs but several Swagger UI
versions silently drop that specific header name when it's sent as a
plain parameter instead of through the security-scheme flow.

Fails CLOSED, not open: if `GATEWAY_API_KEY` isn't configured at all,
every protected request is rejected (503) rather than silently let
through — a missing Kubernetes Secret must never quietly become "no
auth," which an empty-string-means-disabled design would risk.
"""

import hmac
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config.settings import settings

bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway API key is not configured",
        )

    token = credentials.credentials if credentials else ""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header (expected: Bearer <token>)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Constant-time comparison — a plain `!=` would leak how many leading
    # characters matched via response timing, an unnecessary side channel
    # for what should be a real, hard-to-guess secret.
    if not hmac.compare_digest(token, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
