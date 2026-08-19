import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.collectors.base import SourceStatus
from app.collectors.loki_adapter import LokiClient


def run(coro):
    return asyncio.run(coro)


def make_client(handler) -> LokiClient:
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport)
    return LokiClient(base_url="http://loki.test", client=async_client)


def test_query_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/loki/api/v1/query"
        assert request.url.params["query"] == '{namespace="cloudmart-prod"}'
        return httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "streams", "result": []}},
        )

    client = make_client(handler)
    result = run(client.query('{namespace="cloudmart-prod"}'))

    assert result.status == SourceStatus.AVAILABLE
    assert result.ok
    assert result.data["resultType"] == "streams"


def test_query_range_sends_correct_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "streams", "result": []}}
        )

    client = make_client(handler)
    start = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    result = run(client.query_range('{app="order-service"} |= "error"', start, end, limit=500))

    assert captured["path"] == "/loki/api/v1/query_range"
    assert captured["params"]["limit"] == "500"
    assert captured["params"]["direction"] == "backward"
    assert captured["params"]["start"] == str(int(start.timestamp() * 1_000_000_000))
    assert captured["params"]["end"] == str(int(end.timestamp() * 1_000_000_000))
    assert result.status == SourceStatus.AVAILABLE


def test_query_range_rejects_end_before_start():
    client = make_client(lambda r: httpx.Response(200, json={"status": "success", "data": {}}))
    start = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    end = start - timedelta(minutes=5)

    with pytest.raises(ValueError):
        run(client.query_range("{namespace=\"x\"}", start, end))


def test_loki_api_error_status_is_reported_as_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "error", "errorType": "bad_data", "error": "invalid LogQL"},
        )

    client = make_client(handler)
    result = run(client.query("this is not valid logql"))

    assert result.status == SourceStatus.UNAVAILABLE
    assert not result.ok
    assert "invalid LogQL" in result.error


def test_http_5xx_is_reported_as_unavailable_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    client = make_client(handler)
    result = run(client.query('{namespace="x"}'))

    assert result.status == SourceStatus.UNAVAILABLE
    assert "500" in result.error


def test_timeout_is_reported_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = make_client(handler)
    result = run(client.query('{namespace="x"}'))

    assert result.status == SourceStatus.TIMEOUT
    assert not result.ok


def test_connection_error_is_reported_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)
    result = run(client.query('{namespace="x"}'))

    assert result.status == SourceStatus.UNAVAILABLE
    assert "Could not reach Loki" in result.error


def test_base_url_requires_value():
    with pytest.raises(ValueError):
        LokiClient(base_url="")


# --- parse_entries ---------------------------------------------------------


def test_parse_entries_extracts_known_labels_and_message():
    data = {
        "resultType": "streams",
        "result": [
            {
                "stream": {
                    "namespace": "cloudmart-prod",
                    "pod": "order-service-abc123",
                    "container": "order-service",
                    "app": "order-service",
                },
                "values": [
                    ["1755075600000000000", "database connection timeout"],
                ],
            }
        ],
    }

    entries = LokiClient.parse_entries(data)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.namespace == "cloudmart-prod"
    assert entry.pod == "order-service-abc123"
    assert entry.container == "order-service"
    assert entry.service == "order-service"
    assert entry.message == "database connection timeout"
    assert entry.labels == data["result"][0]["stream"]
    assert entry.timestamp == datetime(2025, 8, 13, 9, 0, tzinfo=timezone.utc)


def test_parse_entries_falls_back_through_candidate_label_keys():
    data = {
        "result": [
            {
                "stream": {"kubernetes_namespace_name": "cloudmart-prod", "job": "user-service"},
                "values": [["1755075600000000000", "auth failed"]],
            }
        ]
    }

    entries = LokiClient.parse_entries(data)

    assert entries[0].namespace == "cloudmart-prod"
    assert entries[0].service == "user-service"
    assert entries[0].pod is None


def test_parse_entries_handles_multiple_streams_and_multiple_lines():
    data = {
        "result": [
            {
                "stream": {"app": "a"},
                "values": [
                    ["1755075600000000000", "line 1"],
                    ["1755075601000000000", "line 2"],
                ],
            },
            {
                "stream": {"app": "b"},
                "values": [["1755075602000000000", "line 3"]],
            },
        ]
    }

    entries = LokiClient.parse_entries(data)

    assert len(entries) == 3
    assert [e.message for e in entries] == ["line 1", "line 2", "line 3"]


def test_parse_entries_returns_empty_list_for_empty_or_malformed_data():
    assert LokiClient.parse_entries({}) == []
    assert LokiClient.parse_entries({"result": []}) == []
    assert LokiClient.parse_entries({"result": [{"stream": {}, "values": []}]}) == []


def test_parse_entries_skips_unparseable_timestamp_without_raising():
    data = {
        "result": [
            {
                "stream": {"app": "a"},
                "values": [["not-a-timestamp", "line 1"], ["1755075600000000000", "line 2"]],
            }
        ]
    }

    entries = LokiClient.parse_entries(data)

    assert len(entries) == 1
    assert entries[0].message == "line 2"
