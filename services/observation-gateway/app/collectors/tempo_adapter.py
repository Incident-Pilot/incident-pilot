"""
Tempo adapter — spec section 14.

Like the Prometheus/Loki adapters: generic interface, callers supply the
search parameters or trace ID, this class does not hardcode "the" query
that matters for an incident.

**Unverified assumption, same category as the Loki label-name issue in
loki_adapter.py**: Tempo's `GET /api/traces/{traceID}` endpoint is
documented to implement the Jaeger Query API's trace-JSON shape
(`{"data": [{"traceID", "spans": [...], "processes": {...}}]}`) for
HTTP-API compatibility. This has NOT been confirmed against this
cluster's actual Tempo version/config — some Tempo versions/configs may
return an OTLP-JSON shape instead. `parse_spans()` assumes the Jaeger
shape; if `scripts/live_check_tempo.py` shows spans coming back empty
against a real trace ID, that assumption is wrong and parsing needs to
switch to OTLP.

Every call returns an AdapterResult instead of raising (spec section 29).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, ConfigDict

from .base import AdapterResult, SourceStatus


class Span(BaseModel):
    """A single normalized span, extracted from a Jaeger-shaped Tempo
    trace response. Not the canonical Observation/Evidence model — this is
    adapter-level structure; conversion happens in the normalization layer.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    service: Optional[str] = None
    operation: str
    start_time: datetime
    duration_ms: float
    status: str  # "ok" or "error"
    tags: Dict[str, Any] = {}


class TraceSummary(BaseModel):
    """A single row from a Tempo search result — enough to decide which
    trace(s) are relevant to an incident before fetching the full trace."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    root_service: Optional[str] = None
    root_operation: Optional[str] = None
    start_time: Optional[datetime] = None
    duration_ms: Optional[float] = None


class TempoClient:
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

    async def get_trace(self, trace_id: str) -> AdapterResult[Dict[str, Any]]:
        """Fetch a single trace by ID — TempoClient.get_trace() per spec
        section 14 ("which span produced the error", etc. all start from
        having the full trace)."""
        if not trace_id or not trace_id.strip():
            raise ValueError("trace_id is required")
        return await self._get(f"/api/traces/{trace_id}", {})

    async def search(
        self,
        params: Dict[str, Any],
    ) -> AdapterResult[Dict[str, Any]]:
        """Search for traces — TempoClient.search() per spec section 14.

        Deliberately takes a raw params dict rather than named
        service/tag/duration arguments: Tempo supports both the legacy
        tag-based search (`tags=service.name=order-service`) and TraceQL
        (`q=<traceql>`), and which one this cluster's Tempo version
        supports has not been verified. Callers build whichever query
        shape is appropriate; this method just transports it.
        """
        return await self._get("/api/search", params)

    @staticmethod
    def parse_spans(data: Dict[str, Any]) -> List[Span]:
        """Flatten a raw Jaeger-shaped Tempo trace response into Span
        records, preserving trace ID, span ID, service, operation,
        duration, status, parent/child relationships, and timestamps
        (spec section 14). Defensive by design: malformed/unexpected
        shapes yield an empty list rather than raising, since the
        response-shape assumption above is unverified."""

        spans: List[Span] = []
        for trace in data.get("data", []) or []:
            processes: Dict[str, Any] = trace.get("processes", {}) or {}
            for raw_span in trace.get("spans", []) or []:
                try:
                    span_id = raw_span["spanID"]
                    trace_id = raw_span.get("traceID", trace.get("traceID", ""))
                    operation = raw_span.get("operationName", "")
                    start_time = datetime.fromtimestamp(
                        raw_span["startTime"] / 1_000_000, tz=timezone.utc
                    )
                    duration_ms = raw_span.get("duration", 0) / 1_000
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue

                process_id = raw_span.get("processID")
                service = None
                if process_id and process_id in processes:
                    service = processes[process_id].get("serviceName")

                parent_span_id = None
                for ref in raw_span.get("references", []) or []:
                    if ref.get("refType") == "CHILD_OF":
                        parent_span_id = ref.get("spanID")
                        break

                tags = {
                    t.get("key"): t.get("value")
                    for t in raw_span.get("tags", []) or []
                    if "key" in t
                }
                try:
                    status_code = int(tags["http.status_code"])
                except (KeyError, TypeError, ValueError):
                    status_code = None
                is_error = bool(tags.get("error")) or (
                    status_code is not None and status_code >= 400
                )
                status = "error" if is_error else "ok"

                spans.append(
                    Span(
                        trace_id=trace_id,
                        span_id=span_id,
                        parent_span_id=parent_span_id,
                        service=service,
                        operation=operation,
                        start_time=start_time,
                        duration_ms=duration_ms,
                        status=status,
                        tags=tags,
                    )
                )
        return spans

    @staticmethod
    def parse_search_results(data: Dict[str, Any]) -> List[TraceSummary]:
        """Flatten a raw Tempo /api/search response into TraceSummary
        records. Defensive: skips entries missing a trace ID rather than
        raising."""

        summaries: List[TraceSummary] = []
        for raw in data.get("traces", []) or []:
            trace_id = raw.get("traceID")
            if not trace_id:
                continue

            start_time = None
            raw_start = raw.get("startTimeUnixNano")
            if raw_start is not None:
                try:
                    start_time = datetime.fromtimestamp(
                        int(raw_start) / 1_000_000_000, tz=timezone.utc
                    )
                except (ValueError, TypeError, OverflowError):
                    start_time = None

            duration_ms = raw.get("durationMs")

            summaries.append(
                TraceSummary(
                    trace_id=trace_id,
                    root_service=raw.get("rootServiceName"),
                    root_operation=raw.get("rootTraceName"),
                    start_time=start_time,
                    duration_ms=float(duration_ms) if duration_ms is not None else None,
                )
            )
        return summaries

    async def _get(
        self, path: str, params: Dict[str, Any]
    ) -> AdapterResult[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        owns_client = self._injected_client is None
        client = self._injected_client or httpx.AsyncClient(timeout=self.timeout_seconds)

        try:
            try:
                response = await client.get(url, params=params)
                if response.status_code == 404:
                    return AdapterResult(
                        status=SourceStatus.UNAVAILABLE,
                        error=f"Tempo returned 404 (trace/route not found) for {url}",
                    )
                response.raise_for_status()
                payload = response.json()
            except httpx.TimeoutException as exc:
                return AdapterResult(
                    status=SourceStatus.TIMEOUT,
                    error=f"Tempo request timed out: {exc}",
                )
            except httpx.HTTPStatusError as exc:
                return AdapterResult(
                    status=SourceStatus.UNAVAILABLE,
                    error=(
                        f"Tempo returned HTTP {exc.response.status_code}: "
                        f"{exc.response.text[:300]}"
                    ),
                )
            except httpx.RequestError as exc:
                return AdapterResult(
                    status=SourceStatus.UNAVAILABLE,
                    error=f"Could not reach Tempo at {self.base_url}: {exc}",
                )
        finally:
            if owns_client:
                await client.aclose()

        return AdapterResult(status=SourceStatus.AVAILABLE, data=payload)
