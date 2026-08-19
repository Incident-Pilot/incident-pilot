"""
Loki adapter — spec section 13.

Like the Prometheus adapter, this is deliberately generic: callers supply
the LogQL, this class does not hardcode "the" query for errors/timeouts/
auth-failures/etc. (spec section 12's "do not hard-code one query for the
entire system" applies equally here).

Loki's raw response shape only gives you a per-stream label set plus raw
log lines — it does not label-name-guarantee "namespace"/"pod"/"container"/
"service" the way spec section 13 asks us to preserve. Promtail's actual
label conventions on this cluster have NOT been verified yet (see
docs/PROGRESS.md Task 3 note), so `parse_entries()` resolves each of those
fields from a prioritized list of common Promtail/k8s label names rather
than assuming one fixed key — and always keeps the full raw label set too,
so nothing is silently dropped if the real convention differs.

Every call returns an AdapterResult instead of raising (spec section 29).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .base import AdapterResult, SourceStatus

# Priority-ordered candidate label keys for each semantic field. First match
# wins. Covers common Promtail/kube-state conventions without assuming a
# single one is correct for this cluster.
_NAMESPACE_LABEL_CANDIDATES = ("namespace", "kubernetes_namespace_name", "k8s_namespace")
_POD_LABEL_CANDIDATES = ("pod", "kubernetes_pod_name", "pod_name")
_CONTAINER_LABEL_CANDIDATES = ("container", "kubernetes_container_name", "container_name")
_SERVICE_LABEL_CANDIDATES = ("service", "app", "app_kubernetes_io_name", "job", "container")


def _first_present(labels: Dict[str, str], candidates: tuple) -> Optional[str]:
    for key in candidates:
        if key in labels and labels[key]:
            return labels[key]
    return None


class LogEntry(BaseModel):
    """A single normalized log line extracted from a Loki stream result.
    Not the canonical Observation model — this is adapter-level structure;
    conversion to Observation happens in the normalization layer."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    namespace: Optional[str] = None
    pod: Optional[str] = None
    container: Optional[str] = None
    service: Optional[str] = None
    labels: Dict[str, str] = Field(default_factory=dict)
    message: str


class LokiClient:
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
        self._injected_client = client

    async def query(
        self, logql: str, limit: int = 100, at: Optional[datetime] = None
    ) -> AdapterResult[Dict[str, Any]]:
        """Instant query — LokiClient.query() per spec section 13."""
        params: Dict[str, Any] = {"query": logql, "limit": limit}
        if at is not None:
            params["time"] = str(int(at.timestamp() * 1_000_000_000))
        return await self._get("/loki/api/v1/query", params)

    async def query_range(
        self,
        logql: str,
        start: datetime,
        end: datetime,
        limit: int = 1000,
        direction: str = "backward",
    ) -> AdapterResult[Dict[str, Any]]:
        """Range query — LokiClient.query_range() per spec section 13."""
        if end <= start:
            raise ValueError("end must be after start")
        params: Dict[str, Any] = {
            "query": logql,
            "start": str(int(start.timestamp() * 1_000_000_000)),
            "end": str(int(end.timestamp() * 1_000_000_000)),
            "limit": limit,
            "direction": direction,
        }
        return await self._get("/loki/api/v1/query_range", params)

    @staticmethod
    def parse_entries(data: Dict[str, Any]) -> List[LogEntry]:
        """Flatten a raw Loki `streams`-shaped result into LogEntry records,
        preserving timestamp/namespace/pod/container/service/labels/message
        per spec section 13. Safe to call on empty/malformed data — returns
        an empty list rather than raising, since this runs on adapter output
        that may not always be a streams result (e.g. metric-type LogQL)."""

        entries: List[LogEntry] = []
        for stream in data.get("result", []) or []:
            labels: Dict[str, str] = stream.get("stream", {}) or {}
            for raw_ts, line in stream.get("values", []) or []:
                try:
                    ts = datetime.fromtimestamp(int(raw_ts) / 1_000_000_000, tz=timezone.utc)
                except (ValueError, TypeError, OverflowError):
                    continue
                entries.append(
                    LogEntry(
                        timestamp=ts,
                        namespace=_first_present(labels, _NAMESPACE_LABEL_CANDIDATES),
                        pod=_first_present(labels, _POD_LABEL_CANDIDATES),
                        container=_first_present(labels, _CONTAINER_LABEL_CANDIDATES),
                        service=_first_present(labels, _SERVICE_LABEL_CANDIDATES),
                        labels=labels,
                        message=line,
                    )
                )
        return entries

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
                    error=f"Loki request timed out: {exc}",
                )
            except httpx.HTTPStatusError as exc:
                return AdapterResult(
                    status=SourceStatus.UNAVAILABLE,
                    error=(
                        f"Loki returned HTTP {exc.response.status_code}: "
                        f"{exc.response.text[:300]}"
                    ),
                )
            except httpx.RequestError as exc:
                return AdapterResult(
                    status=SourceStatus.UNAVAILABLE,
                    error=f"Could not reach Loki at {self.base_url}: {exc}",
                )
        finally:
            if owns_client:
                await client.aclose()

        if payload.get("status") != "success":
            return AdapterResult(
                status=SourceStatus.UNAVAILABLE,
                error=(
                    f"Loki API error (status={payload.get('status')!r}): "
                    f"{payload.get('error', 'unknown error')}"
                ),
            )

        return AdapterResult(status=SourceStatus.AVAILABLE, data=payload.get("data"))
