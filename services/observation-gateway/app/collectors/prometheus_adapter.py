"""
Prometheus adapter — spec section 12.

Deliberately generic: this class does not hardcode which PromQL queries
matter ("do not hard-code one query for the entire system"). Callers
(the future Incident Context Builder, or ad-hoc scripts) supply the
PromQL. Selecting *which* queries represent CPU/memory/error-rate/etc.
is a separate, later concern once real metric names are verified against
this cluster's kube-prometheus-stack + Traefik metric exports.

Every call returns an AdapterResult instead of raising, so a Prometheus
outage degrades to partial incident context rather than crashing the
gateway (spec section 29).
"""

from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from .base import AdapterResult, SourceStatus


class PrometheusClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 5.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        if not base_url:
            raise ValueError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        # Allows a caller (tests, or a shared connection pool) to inject
        # an httpx.AsyncClient. If none is given, one is created and torn
        # down per-request.
        self._injected_client = client

    async def query(
        self, promql: str, at: Optional[datetime] = None
    ) -> AdapterResult[Dict[str, Any]]:
        """Instant query — PrometheusClient.query() per spec section 12."""
        params: Dict[str, Any] = {"query": promql}
        if at is not None:
            params["time"] = at.timestamp()
        return await self._get("/api/v1/query", params)

    async def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str = "30s",
    ) -> AdapterResult[Dict[str, Any]]:
        """Range query — PrometheusClient.query_range() per spec section 12."""
        if end <= start:
            raise ValueError("end must be after start")
        params: Dict[str, Any] = {
            "query": promql,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        }
        return await self._get("/api/v1/query_range", params)

    async def _get(
        self, path: str, params: Dict[str, Any]
    ) -> AdapterResult[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        owns_client = self._injected_client is None
        client = self._injected_client or httpx.AsyncClient(timeout=self.timeout_seconds)

        try:
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
            except httpx.TimeoutException as exc:
                return AdapterResult(
                    status=SourceStatus.TIMEOUT,
                    error=f"Prometheus request timed out: {exc}",
                )
            except httpx.HTTPStatusError as exc:
                return AdapterResult(
                    status=SourceStatus.UNAVAILABLE,
                    error=(
                        f"Prometheus returned HTTP {exc.response.status_code}: "
                        f"{exc.response.text[:300]}"
                    ),
                )
            except httpx.RequestError as exc:
                return AdapterResult(
                    status=SourceStatus.UNAVAILABLE,
                    error=f"Could not reach Prometheus at {self.base_url}: {exc}",
                )
        finally:
            if owns_client:
                await client.aclose()

        if payload.get("status") != "success":
            return AdapterResult(
                status=SourceStatus.UNAVAILABLE,
                error=(
                    f"Prometheus API error (status={payload.get('status')!r}): "
                    f"{payload.get('error', 'unknown error')}"
                ),
            )

        return AdapterResult(status=SourceStatus.AVAILABLE, data=payload.get("data"))
