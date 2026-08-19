import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.collectors.base import SourceStatus
from app.collectors.tempo_adapter import TempoClient


def run(coro):
    return asyncio.run(coro)


def make_client(handler) -> TempoClient:
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport)
    return TempoClient(base_url="http://tempo.test", client=async_client)


def test_get_trace_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/traces/abc123"
        return httpx.Response(200, json={"data": [{"traceID": "abc123", "spans": []}]})

    client = make_client(handler)
    result = run(client.get_trace("abc123"))

    assert result.status == SourceStatus.AVAILABLE
    assert result.data["data"][0]["traceID"] == "abc123"


def test_get_trace_requires_trace_id():
    client = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        run(client.get_trace(""))


def test_get_trace_404_is_reported_as_unavailable_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="trace not found")

    client = make_client(handler)
    result = run(client.get_trace("nonexistent"))

    assert result.status == SourceStatus.UNAVAILABLE
    assert "404" in result.error


def test_search_sends_arbitrary_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"traces": []})

    client = make_client(handler)
    result = run(
        client.search({"tags": "service.name=order-service", "start": "1000", "end": "2000"})
    )

    assert captured["path"] == "/api/search"
    assert captured["params"]["tags"] == "service.name=order-service"
    assert result.status == SourceStatus.AVAILABLE


def test_timeout_is_reported_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = make_client(handler)
    result = run(client.get_trace("abc123"))

    assert result.status == SourceStatus.TIMEOUT


def test_connection_error_is_reported_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)
    result = run(client.search({}))

    assert result.status == SourceStatus.UNAVAILABLE
    assert "Could not reach Tempo" in result.error


def test_http_5xx_is_reported_as_unavailable_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    client = make_client(handler)
    result = run(client.search({}))

    assert result.status == SourceStatus.UNAVAILABLE
    assert "500" in result.error


def test_base_url_requires_value():
    with pytest.raises(ValueError):
        TempoClient(base_url="")


# --- parse_spans ------------------------------------------------------------


def test_parse_spans_extracts_service_operation_duration_and_status():
    data = {
        "data": [
            {
                "traceID": "trace1",
                "spans": [
                    {
                        "traceID": "trace1",
                        "spanID": "span1",
                        "operationName": "GET /orders",
                        "startTime": 1755075600000000,
                        "duration": 15000,
                        "processID": "p1",
                        "tags": [{"key": "http.status_code", "value": 500}],
                        "references": [],
                    }
                ],
                "processes": {"p1": {"serviceName": "order-service"}},
            }
        ]
    }

    spans = TempoClient.parse_spans(data)

    assert len(spans) == 1
    span = spans[0]
    assert span.trace_id == "trace1"
    assert span.span_id == "span1"
    assert span.service == "order-service"
    assert span.operation == "GET /orders"
    assert span.duration_ms == 15.0
    assert span.status == "error"
    assert span.parent_span_id is None
    assert span.start_time == datetime(2025, 8, 13, 9, 0, tzinfo=timezone.utc)


def test_parse_spans_extracts_parent_child_relationship():
    data = {
        "data": [
            {
                "traceID": "trace1",
                "spans": [
                    {
                        "traceID": "trace1",
                        "spanID": "child-span",
                        "operationName": "call product-service",
                        "startTime": 1755075600000000,
                        "duration": 5000,
                        "processID": "p1",
                        "tags": [],
                        "references": [
                            {"refType": "CHILD_OF", "traceID": "trace1", "spanID": "parent-span"}
                        ],
                    }
                ],
                "processes": {"p1": {"serviceName": "product-service"}},
            }
        ]
    }

    spans = TempoClient.parse_spans(data)

    assert spans[0].parent_span_id == "parent-span"


def test_parse_spans_marks_ok_status_when_no_error_indicators():
    data = {
        "data": [
            {
                "traceID": "trace1",
                "spans": [
                    {
                        "traceID": "trace1",
                        "spanID": "span1",
                        "operationName": "GET /health",
                        "startTime": 1755075600000000,
                        "duration": 1000,
                        "processID": "p1",
                        "tags": [{"key": "http.status_code", "value": 200}],
                        "references": [],
                    }
                ],
                "processes": {"p1": {"serviceName": "order-service"}},
            }
        ]
    }

    spans = TempoClient.parse_spans(data)

    assert spans[0].status == "ok"


def test_parse_spans_treats_error_tag_true_as_error_even_without_status_code():
    data = {
        "data": [
            {
                "traceID": "trace1",
                "spans": [
                    {
                        "traceID": "trace1",
                        "spanID": "span1",
                        "operationName": "db query",
                        "startTime": 1755075600000000,
                        "duration": 1000,
                        "processID": "p1",
                        "tags": [{"key": "error", "value": True}],
                        "references": [],
                    }
                ],
                "processes": {"p1": {"serviceName": "order-service"}},
            }
        ]
    }

    spans = TempoClient.parse_spans(data)

    assert spans[0].status == "error"


def test_parse_spans_returns_empty_list_for_empty_or_malformed_data():
    assert TempoClient.parse_spans({}) == []
    assert TempoClient.parse_spans({"data": []}) == []
    assert TempoClient.parse_spans({"data": [{"spans": []}]}) == []


def test_parse_spans_skips_span_missing_required_fields_without_raising():
    data = {
        "data": [
            {
                "traceID": "trace1",
                "spans": [
                    {"spanID": "span1"},  # missing operationName/startTime/duration keys
                    {
                        "traceID": "trace1",
                        "spanID": "span2",
                        "operationName": "ok span",
                        "startTime": 1755075600000000,
                        "duration": 1000,
                        "processID": "p1",
                        "tags": [],
                        "references": [],
                    },
                ],
                "processes": {"p1": {"serviceName": "order-service"}},
            }
        ]
    }

    spans = TempoClient.parse_spans(data)

    assert len(spans) == 1
    assert spans[0].span_id == "span2"


# --- parse_search_results -----------------------------------------------


def test_parse_search_results_extracts_summary_fields():
    data = {
        "traces": [
            {
                "traceID": "trace1",
                "rootServiceName": "order-service",
                "rootTraceName": "POST /orders",
                "startTimeUnixNano": "1755075600000000000",
                "durationMs": 42.5,
            }
        ]
    }

    summaries = TempoClient.parse_search_results(data)

    assert len(summaries) == 1
    s = summaries[0]
    assert s.trace_id == "trace1"
    assert s.root_service == "order-service"
    assert s.root_operation == "POST /orders"
    assert s.duration_ms == 42.5
    assert s.start_time == datetime(2025, 8, 13, 9, 0, tzinfo=timezone.utc)


def test_parse_search_results_skips_entries_missing_trace_id():
    data = {"traces": [{"rootServiceName": "order-service"}]}

    assert TempoClient.parse_search_results(data) == []


def test_parse_search_results_returns_empty_list_for_empty_data():
    assert TempoClient.parse_search_results({}) == []
