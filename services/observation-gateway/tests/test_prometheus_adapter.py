import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.collectors.base import SourceStatus
from app.collectors.prometheus_adapter import PrometheusClient


def run(coro):
    return asyncio.run(coro)


def make_client(handler) -> PrometheusClient:
    transport = httpx.MockTransport(handler)
    async_client = httpx.AsyncClient(transport=transport)
    return PrometheusClient(base_url="http://prometheus.test", client=async_client)


def test_query_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/query"
        assert request.url.params["query"] == 'up{job="order-service"}'
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {}, "value": [1234, "1"]}],
                },
            },
        )

    client = make_client(handler)
    result = run(client.query('up{job="order-service"}'))

    assert result.status == SourceStatus.AVAILABLE
    assert result.ok
    assert result.data["resultType"] == "vector"
    assert result.error is None


def test_query_range_sends_correct_params():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"status": "success", "data": {"resultType": "matrix", "result": []}}
        )

    client = make_client(handler)
    start = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=15)
    result = run(client.query_range("rate(http_requests_total[5m])", start, end, step="30s"))

    assert captured["path"] == "/api/v1/query_range"
    assert captured["params"]["step"] == "30s"
    assert captured["params"]["query"] == "rate(http_requests_total[5m])"
    assert result.status == SourceStatus.AVAILABLE


def test_query_range_rejects_end_before_start():
    client = make_client(lambda r: httpx.Response(200, json={"status": "success", "data": {}}))
    start = datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc)
    end = start - timedelta(minutes=5)

    with pytest.raises(ValueError):
        run(client.query_range("up", start, end))


def test_prometheus_api_error_status_is_reported_as_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "error", "errorType": "bad_data", "error": "invalid PromQL"},
        )

    client = make_client(handler)
    result = run(client.query("this is not valid promql"))

    assert result.status == SourceStatus.UNAVAILABLE
    assert not result.ok
    assert "invalid PromQL" in result.error


def test_http_5xx_is_reported_as_unavailable_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    client = make_client(handler)
    result = run(client.query("up"))

    assert result.status == SourceStatus.UNAVAILABLE
    assert "500" in result.error


def test_timeout_is_reported_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = make_client(handler)
    result = run(client.query("up"))

    assert result.status == SourceStatus.TIMEOUT
    assert not result.ok


def test_connection_error_is_reported_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)
    result = run(client.query("up"))

    assert result.status == SourceStatus.UNAVAILABLE
    assert "Could not reach Prometheus" in result.error


def test_base_url_requires_value():
    with pytest.raises(ValueError):
        PrometheusClient(base_url="")
