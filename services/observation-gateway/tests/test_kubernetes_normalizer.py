from datetime import datetime, timezone

from app.collectors.kubernetes_adapter import ContainerStatusSummary, K8sEvent, PodSummary
from app.normalizers.kubernetes_normalizer import normalize_events, normalize_pod_statuses
from shared.models import Severity


def make_event(**overrides) -> K8sEvent:
    defaults = dict(
        reason="CrashLoopBackOff",
        message="Back-off restarting failed container",
        resource="Pod/order-service-abc123",
        namespace="cloudmart-prod",
        timestamp=datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc),
        severity="Warning",
        count=3,
    )
    defaults.update(overrides)
    return K8sEvent(**defaults)


def test_normalize_events_maps_warning_to_warning_severity():
    observations = normalize_events([make_event()], cluster="cloudmart-k3s")
    assert observations[0].severity == Severity.WARNING
    assert observations[0].signal == "CrashLoopBackOff"
    assert observations[0].resource == "Pod/order-service-abc123"


def test_normalize_events_maps_normal_to_info_severity():
    observations = normalize_events([make_event(severity="Normal", reason="Scheduled")], cluster="c")
    assert observations[0].severity == Severity.INFO


def test_normalize_events_unknown_type_maps_to_unknown():
    observations = normalize_events([make_event(severity="SomethingElse")], cluster="c")
    assert observations[0].severity == Severity.UNKNOWN


def test_normalize_events_missing_timestamp_does_not_raise():
    observations = normalize_events([make_event(timestamp=None)], cluster="c")
    assert observations[0].timestamp.tzinfo is not None


def make_pod(**overrides) -> PodSummary:
    defaults = dict(
        name="order-service-abc123",
        namespace="cloudmart-prod",
        phase="Running",
        ready=True,
        restart_count=0,
        containers=[],
        created_at=datetime(2026, 8, 19, 9, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return PodSummary(**defaults)


def test_normalize_pod_statuses_healthy_pod_is_info():
    observations = normalize_pod_statuses([make_pod()], cluster="c")
    assert observations[0].severity == Severity.INFO
    assert observations[0].signal == "pod_status"


def test_normalize_pod_statuses_not_ready_pod_is_warning():
    observations = normalize_pod_statuses([make_pod(ready=False)], cluster="c")
    assert observations[0].severity == Severity.WARNING


def test_normalize_pod_statuses_crash_looping_container_is_warning():
    crashing_container = ContainerStatusSummary(
        name="order-service", ready=False, restart_count=5, state="waiting", reason="CrashLoopBackOff"
    )
    observations = normalize_pod_statuses(
        [make_pod(ready=True, containers=[crashing_container])], cluster="c"
    )
    assert observations[0].severity == Severity.WARNING
    assert observations[0].metadata["reasons"] == ["CrashLoopBackOff"]
