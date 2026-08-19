import asyncio
from datetime import datetime, timedelta, timezone

from app.correlation.incident_correlator import correlate_or_create_incident
from app.models.alertmanager import AlertmanagerWebhookPayload
from app.storage.memory import InMemoryIncidentStore
from shared.models import Correlation, IncidentStatus, Observation, ObservationSource, Severity, SignalType


def run(coro):
    return asyncio.run(coro)


def make_observation(**overrides) -> Observation:
    defaults = dict(
        source=ObservationSource.ALERTMANAGER,
        signal_type=SignalType.ALERT,
        severity=Severity.CRITICAL,
        cluster="cloudmart-k3s",
        namespace="cloudmart-prod",
        service="order-service",
        resource="order-service-abc123",
        signal="HighHTTPErrorRate",
        labels={"alertname": "HighHTTPErrorRate", "severity": "critical"},
        correlation=Correlation(),
    )
    defaults.update(overrides)
    return Observation.new(**defaults)


def make_payload(**overrides) -> AlertmanagerWebhookPayload:
    defaults = dict(
        status="firing",
        groupLabels={"alertname": "HighHTTPErrorRate", "namespace": "cloudmart-prod"},
        alerts=[
            {
                "status": "firing",
                "labels": {"alertname": "HighHTTPErrorRate"},
                "annotations": {},
                "startsAt": "2026-08-19T09:30:00Z",
            }
        ],
    )
    defaults.update(overrides)
    return AlertmanagerWebhookPayload(**defaults)


def test_first_delivery_creates_new_incident():
    store = InMemoryIncidentStore()
    incident = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    assert incident.incident_id.startswith("INC-")
    assert incident.status == IncidentStatus.OPEN
    assert incident.affected_services == ["order-service"]
    assert incident.initial_alerts == ["HighHTTPErrorRate"]


def test_second_delivery_same_namespace_service_within_window_merges():
    store = InMemoryIncidentStore()
    first = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    second_obs = make_observation(signal="HighLatency", severity=Severity.WARNING)
    second_payload = make_payload(
        groupLabels={"alertname": "HighLatency", "namespace": "cloudmart-prod"}
    )
    second = run(correlate_or_create_incident([second_obs], second_payload, store))

    assert second.incident_id == first.incident_id
    assert sorted(second.initial_alerts) == ["HighHTTPErrorRate", "HighLatency"]
    assert second.affected_services == ["order-service"]
    # severity stays at the higher of the two (critical beats warning)
    assert second.severity == Severity.CRITICAL
    assert second.updated_at > first.updated_at

    all_incidents = run(store.list_all())
    assert len(all_incidents) == 1


def test_duplicate_alert_refiring_does_not_create_a_new_incident():
    store = InMemoryIncidentStore()
    first = run(correlate_or_create_incident([make_observation()], make_payload(), store))
    second = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    assert second.incident_id == first.incident_id
    assert second.initial_alerts == ["HighHTTPErrorRate"]  # no duplicate entry
    assert len(run(store.list_all())) == 1


def test_different_namespace_creates_separate_incident():
    store = InMemoryIncidentStore()
    first = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    other_ns_obs = make_observation(namespace="cloudmart-staging")
    other_payload = make_payload(
        groupLabels={"alertname": "HighHTTPErrorRate", "namespace": "cloudmart-staging"}
    )
    second = run(correlate_or_create_incident([other_ns_obs], other_payload, store))

    assert second.incident_id != first.incident_id
    assert len(run(store.list_all())) == 2


def test_no_overlapping_service_creates_separate_incident():
    store = InMemoryIncidentStore()
    first = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    other_service_obs = make_observation(service="product-service")
    second = run(correlate_or_create_incident([other_service_obs], make_payload(), store))

    assert second.incident_id != first.incident_id
    assert len(run(store.list_all())) == 2


def test_observation_with_no_derivable_service_never_merges():
    store = InMemoryIncidentStore()
    run(correlate_or_create_incident([make_observation()], make_payload(), store))

    no_service_obs = make_observation(service=None)
    payload_no_ns_hint = make_payload(groupLabels={"alertname": "HighHTTPErrorRate"})
    second = run(correlate_or_create_incident([no_service_obs], payload_no_ns_hint, store))

    assert len(run(store.list_all())) == 2
    assert second.affected_services == []


def test_outside_correlation_window_creates_separate_incident():
    store = InMemoryIncidentStore()
    first = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    # simulate the first incident having gone stale (older than the
    # correlation window) by directly rewriting its updated_at
    stale = first.model_copy(
        update={"updated_at": datetime.now(timezone.utc) - timedelta(minutes=60)}
    )
    run(store.save(stale))

    second = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    assert second.incident_id != first.incident_id
    assert len(run(store.list_all())) == 2


def test_resolved_incident_is_not_a_merge_candidate():
    store = InMemoryIncidentStore()
    first = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    resolved = first.model_copy(update={"status": IncidentStatus.RESOLVED})
    run(store.save(resolved))

    second = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    assert second.incident_id != first.incident_id
    assert len(run(store.list_all())) == 2


def test_multiple_candidates_tie_break_to_most_recently_updated():
    store = InMemoryIncidentStore()
    older = run(correlate_or_create_incident([make_observation()], make_payload(), store))

    # a second, independently-created OPEN incident that also matches
    # namespace + service (contrived directly via the store rather than
    # through correlation, to set up a genuine multi-candidate scenario)
    newer = older.model_copy(
        update={
            "incident_id": "INC-NEWERONE",
            "updated_at": datetime.now(timezone.utc),
        }
    )
    run(store.save(newer))

    third_obs = make_observation(signal="PodRestarting")
    result = run(correlate_or_create_incident([third_obs], make_payload(), store))

    assert result.incident_id == newer.incident_id
