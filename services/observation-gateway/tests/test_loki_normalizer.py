from datetime import datetime, timezone

from app.collectors.loki_adapter import LogEntry
from app.normalizers.loki_normalizer import normalize_log_entries
from shared.models import Severity


def make_entry(**overrides) -> LogEntry:
    defaults = dict(
        timestamp=datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc),
        namespace="cloudmart-prod",
        pod="order-service-abc123",
        container="order-service",
        service="order-service",
        labels={"namespace": "cloudmart-prod"},
        message="database connection timeout",
    )
    defaults.update(overrides)
    return LogEntry(**defaults)


def test_normalize_log_entries_basic_fields():
    observations = normalize_log_entries([make_entry()], cluster="cloudmart-k3s")
    assert len(observations) == 1
    obs = observations[0]
    assert obs.signal == "log_line"
    assert obs.service == "order-service"
    assert obs.resource == "order-service-abc123"
    assert obs.metadata["message"] == "database connection timeout"
    assert obs.severity == Severity.UNKNOWN  # no free-text inference


def test_normalize_log_entries_caps_at_max_entries():
    entries = [make_entry() for _ in range(10)]
    observations = normalize_log_entries(entries, cluster="c", max_entries=3)
    assert len(observations) == 3


def test_normalize_log_entries_empty_list():
    assert normalize_log_entries([], cluster="c") == []
