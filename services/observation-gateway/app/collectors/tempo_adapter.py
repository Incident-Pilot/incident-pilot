"""
Tempo adapter — spec section 14.

Like the Prometheus/Loki adapters: generic interface, callers supply the
search parameters or trace ID, this class does not hardcode "the" query
that matters for an incident.

**Response shape — CONFIRMED against the live cluster (Tempo 2.9.0,
kube-prometheus-stack-adjacent deploy).** `GET /api/traces/{traceID}`
returns OTLP-JSON, not the Jaeger-shaped trace JSON originally assumed:

    {"batches": [{"resource": {"attributes": [...]}, "scopeSpans": [...]}]}

(the spans container key is either `scopeSpans` or the older
`instrumentationLibrarySpans` — both are handled below since which one a
given Tempo build/OTel Collector version emits wasn't pinned down, and
detecting it costs nothing).

Two shape quirks worth knowing about, confirmed from a real captured span:

- `traceId`/`spanId`/`parentSpanId` are **base64-encoded** raw bytes, not
  hex strings — e.g. `"If4keIFqETeD782LnRuuyQ=="` for a trace ID. Tempo's
  `/api/search` endpoint, by contrast, already returns trace IDs as lowercase
  hex (e.g. `"27fa1ecffca62ede46aabb3bcaf4ece1"`). `parse_spans()` decodes
  the base64 IDs to the same lowercase-hex form so a trace ID from `search()`
  and the same trace's spans from `get_trace()` are directly comparable —
  without this, incident correlation logic joining search results to fetched
  traces would silently never match.
- `attributes` (both on `resource` and on each span) is a list of
  `{"key": ..., "value": {"stringValue": ...}}` typed-union pairs, not a
  flat dict — same shape as Loki's raw stream labels being unverified
  required a lookup helper, this needs one too (`_attrs_to_dict` below).
  `service.name` lives on the *resource*, not the span itself.

The old Jaeger-shape parser is kept as `_parse_spans_jaeger` and used only
if a response ever comes back with a top-level `"data"` key instead of
`"batches"` — defensive, in case a different Tempo config/version in some
other environment genuinely returns that shape. `parse_spans()` dispatches
on whichever key is present.

**Known noise source, not yet filtered here (deliberately — this adapter
stays a dumb transport/parse layer per spec section 7's "no reasoning in
the gateway" boundary; filtering belongs in normalization/enrichment):**
kubelet liveness/readiness probes (`GET /health`, `GET /ready`) generate
real spans too, tagged `http.user_agent: kube-probe/<version>`. Every
span's raw attributes are preserved in `tags`, so a downstream layer can
filter these out by checking `tags.get("http.user_agent", "").startswith("kube-probe")`
without this adapter needing to make that judgment call itself.

Every call returns an AdapterResult instead of raising (spec section 29).
"""

import base64
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, ConfigDict

from .base import AdapterResult, SourceStatus


class Span(BaseModel):
    """A single normalized span. trace_id/span_id/parent_span_id are always
    lowercase hex strings regardless of whether the source response encoded
    them as base64 (OTLP) or hex (legacy Jaeger-shape) — see module
    docstring. Not the canonical Observation/Evidence model — this is
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


def _b64_to_hex(value: str) -> str:
    """Decode a base64 OTLP trace/span ID into the lowercase hex form used
    everywhere else (search results, log correlation, human-readable IDs).
    Falls back to returning the input unchanged if it isn't valid base64 —
    defensive, since a malformed ID shouldn't take down parsing of the rest
    of the trace."""
    try:
        return base64.b64decode(value).hex()
    except (ValueError, TypeError):
        return value


def _attr_value(value: Dict[str, Any]) -> Any:
    """Extract the actual value out of an OTLP typed-union attribute value
    dict, e.g. {"stringValue": "x"} -> "x", {"intValue": "8001"} -> 8001."""
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "intValue" in value:
        try:
            return int(value["intValue"])
        except (TypeError, ValueError):
            return value["intValue"]
    if "doubleValue" in value:
        return value["doubleValue"]
    if "arrayValue" in value:
        return [_attr_value(v) for v in value["arrayValue"].get("values", []) or []]
    if "kvlistValue" in value:
        return {
            kv.get("key"): _attr_value(kv.get("value", {}))
            for kv in value["kvlistValue"].get("values", []) or []
            if "key" in kv
        }
    if "bytesValue" in value:
        return value["bytesValue"]
    return None


def _attrs_to_dict(attributes: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Flatten an OTLP attributes list into a plain dict. Used for both
    resource-level attributes (where service.name lives) and span-level
    attributes (which become Span.tags)."""
    result: Dict[str, Any] = {}
    for attr in attributes or []:
        key = attr.get("key")
        if key is None:
            continue
        result[key] = _attr_value(attr.get("value", {}) or {})
    return result


def _is_error_span(tags: Dict[str, Any], status_obj: Dict[str, Any]) -> bool:
    """An OTLP span is an error if its own status.code says so (proto3 JSON
    renders this as either the string "STATUS_CODE_ERROR" or, depending on
    the exporter's JSON marshalling options, the raw int 2 — handle both),
    or — same fallback used for the legacy Jaeger path — if it carries an
    http.status_code attribute >= 400."""
    status_code = (status_obj or {}).get("code")
    if status_code in ("STATUS_CODE_ERROR", 2):
        return True
    try:
        return int(tags.get("http.status_code")) >= 400
    except (TypeError, ValueError):
        return False


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
        (`q=<traceql>`). Confirmed working on this cluster: tag-based
        search via `{"tags": "service.name=order-service", "limit": N}`.
        Callers build whichever query shape is appropriate; this method
        just transports it.
        """
        return await self._get("/api/search", params)

    @staticmethod
    def parse_spans(data: Dict[str, Any]) -> List[Span]:
        """Flatten a raw Tempo trace response into Span records, preserving
        trace ID, span ID, service, operation, duration, status,
        parent/child relationships, and timestamps (spec section 14).
        Dispatches on response shape: OTLP (`"batches"` key — confirmed
        live) or legacy Jaeger (`"data"` key — kept as a defensive
        fallback, unverified against any real cluster). Safe on
        empty/malformed data — returns an empty list rather than raising."""
        if not data:
            return []
        if "batches" in data:
            return TempoClient._parse_spans_otlp(data)
        if "data" in data:
            return TempoClient._parse_spans_jaeger(data)
        return []

    @staticmethod
    def _parse_spans_otlp(data: Dict[str, Any]) -> List[Span]:
        spans: List[Span] = []
        for batch in data.get("batches", []) or []:
            resource = batch.get("resource", {}) or {}
            resource_attrs = _attrs_to_dict(resource.get("attributes"))
            service = resource_attrs.get("service.name")

            spans_key = "scopeSpans" if "scopeSpans" in batch else "instrumentationLibrarySpans"
            for scope_span in batch.get(spans_key, []) or []:
                for raw_span in scope_span.get("spans", []) or []:
                    try:
                        span_id = _b64_to_hex(raw_span["spanId"])
                        trace_id = _b64_to_hex(raw_span.get("traceId", ""))
                        operation = raw_span.get("name", "")
                        start_ns = int(raw_span["startTimeUnixNano"])
                        end_ns = int(raw_span.get("endTimeUnixNano", start_ns))
                        start_time = datetime.fromtimestamp(
                            start_ns / 1_000_000_000, tz=timezone.utc
                        )
                        duration_ms = (end_ns - start_ns) / 1_000_000
                    except (KeyError, TypeError, ValueError, OverflowError):
                        continue

                    raw_parent = raw_span.get("parentSpanId")
                    parent_span_id = _b64_to_hex(raw_parent) if raw_parent else None

                    tags = _attrs_to_dict(raw_span.get("attributes"))
                    status_obj = raw_span.get("status") or {}
                    status = "error" if _is_error_span(tags, status_obj) else "ok"

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
    def _parse_spans_jaeger(data: Dict[str, Any]) -> List[Span]:
        """Legacy path for a Jaeger-shaped trace response. Unverified
        against any real cluster so far — kept only as a defensive
        fallback in case some other Tempo deployment genuinely returns
        this shape."""
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
                status = "error" if _is_error_span(tags, {}) else "ok"

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