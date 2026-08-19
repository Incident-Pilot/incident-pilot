from datetime import timezone

from app.models.alertmanager import AlertmanagerAlert
from app.normalizers.alertmanager_normalizer import normalize_alert
from shared.models import ObservationSource, Severity, SignalType


def make_alert(**overrides) -> AlertmanagerAlert:
    defaults = dict(
        status="firing",
        labels={"alertname": "HighHTTPErrorRate", "severity": "critical"},
        annotations={"summary": "error rate spiked"},
        startsAt="2026-08-19T09:30:00Z",
        endsAt=None,
        generatorURL="http://prometheus/graph",
        fingerprint="abc123",
    )
    defaults.update(overrides)
    return AlertmanagerAlert(**defaults)


def test_normalize_firing_alert_basic_fields():
    alert = make_alert()
    obs = normalize_alert(alert, cluster="cloudmart-k3s")

    assert obs.source == ObservationSource.ALERTMANAGER
    assert obs.signal_type == SignalType.ALERT
    assert obs.severity == Severity.CRITICAL
    assert obs.cluster == "cloudmart-k3s"
    assert obs.signal == "HighHTTPErrorRate"
    assert obs.metadata["alert_status"] == "firing"
    assert obs.metadata["fingerprint"] == "abc123"
    assert obs.timestamp.tzinfo is not None


def test_severity_mapping_covers_known_values():
    for label_value, expected in [
        ("critical", Severity.CRITICAL),
        ("warning", Severity.WARNING),
        ("info", Severity.INFO),
        ("none", Severity.INFO),
    ]:
        alert = make_alert(labels={"alertname": "X", "severity": label_value})
        obs = normalize_alert(alert, cluster="c")
        assert obs.severity == expected


def test_unknown_severity_label_maps_to_unknown():
    alert = make_alert(labels={"alertname": "X", "severity": "bogus"})
    obs = normalize_alert(alert, cluster="c")
    assert obs.severity == Severity.UNKNOWN


def test_missing_severity_label_maps_to_unknown():
    alert = make_alert(labels={"alertname": "X"})
    obs = normalize_alert(alert, cluster="c")
    assert obs.severity == Severity.UNKNOWN


def test_missing_alertname_falls_back_to_unknown_alert():
    alert = make_alert(labels={"severity": "warning"})
    obs = normalize_alert(alert, cluster="c")
    assert obs.signal == "unknown_alert"


def test_service_namespace_resource_label_candidates():
    alert = make_alert(
        labels={
            "alertname": "PodCrashLooping",
            "namespace": "cloudmart-prod",
            "service": "order-service",
            "pod": "order-service-abc123",
        }
    )
    obs = normalize_alert(alert, cluster="c")
    assert obs.namespace == "cloudmart-prod"
    assert obs.service == "order-service"
    assert obs.resource == "order-service-abc123"


def test_service_label_falls_back_to_job_when_service_missing():
    alert = make_alert(labels={"alertname": "X", "job": "product-service"})
    obs = normalize_alert(alert, cluster="c")
    assert obs.service == "product-service"


def test_per_alert_labels_win_over_common_labels():
    alert = make_alert(labels={"alertname": "X", "service": "order-service"})
    obs = normalize_alert(
        alert, cluster="c", common_labels={"service": "common-fallback", "team": "sre"}
    )
    assert obs.service == "order-service"
    assert obs.labels["team"] == "sre"


def test_common_labels_fill_in_when_alert_missing_the_key():
    alert = make_alert(labels={"alertname": "X"})
    obs = normalize_alert(alert, cluster="c", common_labels={"namespace": "cloudmart-prod"})
    assert obs.namespace == "cloudmart-prod"


def test_resolved_alert_uses_endsAt_timestamp():
    alert = make_alert(
        status="resolved",
        startsAt="2026-08-19T09:30:00Z",
        endsAt="2026-08-19T09:45:00Z",
    )
    obs = normalize_alert(alert, cluster="c")
    assert obs.timestamp.astimezone(timezone.utc).isoformat().startswith("2026-08-19T09:45:00")


def test_malformed_timestamp_does_not_raise():
    alert = make_alert(startsAt="not-a-timestamp")
    obs = normalize_alert(alert, cluster="c")
    assert obs.timestamp.tzinfo is not None


def test_observation_id_is_generated_and_unique():
    alert = make_alert()
    obs1 = normalize_alert(alert, cluster="c")
    obs2 = normalize_alert(alert, cluster="c")
    assert obs1.observation_id != obs2.observation_id
