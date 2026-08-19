from datetime import datetime, timezone

from app.collectors.tempo_adapter import Span
from app.normalizers.tempo_normalizer import normalize_error_spans
from shared.models import Severity


def make_span(**overrides) -> Span:
    defaults = dict(
        trace_id="trace-1",
        span_id="span-1",
        service="order-service",
        operation="POST /orders",
        start_time=datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc),
        duration_ms=120.0,
        status="error",
        tags={"http.status_code": "500"},
    )
    defaults.update(overrides)
    return Span(**defaults)


def test_normalize_error_spans_only_includes_error_status():
    spans = [make_span(status="error"), make_span(span_id="span-2", status="ok")]
    observations = normalize_error_spans(spans, cluster="cloudmart-k3s")
    assert len(observations) == 1
    assert observations[0].metadata["span_id"] == "span-1"


def test_normalize_error_spans_severity_and_correlation():
    observations = normalize_error_spans([make_span()], cluster="c")
    obs = observations[0]
    assert obs.severity == Severity.WARNING
    assert obs.correlation.trace_id == "trace-1"
    assert obs.service == "order-service"
    assert obs.signal == "trace_error"


def test_normalize_error_spans_empty_list():
    assert normalize_error_spans([], cluster="c") == []


def test_normalize_error_spans_all_ok_returns_empty():
    spans = [make_span(status="ok")]
    assert normalize_error_spans(spans, cluster="c") == []
