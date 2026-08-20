"""
API authentication — spec section 12: "do not expose this
unauthenticated." A single shared bearer-token check, applied per-router
via `APIRouter(dependencies=[Depends(require_api_key)])` to every route
except `/health` and `/ready` (those are defined directly on the app in
main.py, outside any authenticated router — k8s liveness/readiness probes
hit them unauthenticated by convention, and they reveal nothing
sensitive).

Fails CLOSED, not open: if `GATEWAY_API_KEY` isn't configured at all,
every protected request is rejected (503) rather than silently let
through — a missing Kubernetes Secret must never quietly become "no
auth," which an empty-string-means-disabled design would risk.
"""

import hmac

from fastapi import Header, HTTPException, status

from app.config.settings import settings


async def require_api_key(authorization: str = Header(default="")) -> None:
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway API key is not configured",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
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
