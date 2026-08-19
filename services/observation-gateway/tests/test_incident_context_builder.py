import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.collectors.base import AdapterResult, SourceStatus
from app.collectors.kubernetes_adapter import KubernetesClient
from app.collectors.loki_adapter import LokiClient
from app.collectors.prometheus_adapter import PrometheusClient
from app.collectors.tempo_adapter import TempoClient
from app.context.incident_context_builder import IncidentContextBuilder
from app.storage.memory import InMemoryEvidenceStore, InMemoryIncidentStore, InMemoryObservationStore
from shared.models import Incident, IncidentPhase, Severity


def run(coro):
    return asyncio.run(coro)


def make_incident(**overrides) -> Incident:
    now = datetime.now(timezone.utc)
    defaults = dict(
        incident_id="INC-TEST0001",
        title="HighHTTPErrorRate",
        severity=Severity.CRITICAL,
        created_at=now,
        updated_at=now,
        source="alertmanager",
        affected_services=["order-service"],
        affected_namespace="cloudmart-prod",
        initial_alerts=["HighHTTPErrorRate"],
    )
    defaults.update(overrides)
    return Incident(**defaults)


PROM_RANGE_DATA = {
    "result": [
        {
            "metric": {"pod": "order-service-abc123"},
            "values": [[1000.0, "1"], [1030.0, "3"]],
        }
    ]
}

LOKI_STREAMS_DATA = {
    "result": [
        {
            "stream": {"namespace": "cloudmart-prod", "service": "order-service"},
            "values": [["1000000000", "database connection timeout"]],
        }
    ]
}

TEMPO_SEARCH_DATA = {"traces": [{"traceID": "trace-1", "rootServiceName": "order-service"}]}

TEMPO_TRACE_DATA = {
    "data": [
        {
            "traceID": "trace-1",
            "processes": {"p1": {"serviceName": "order-service"}},
            "spans": [
                {
                    "spanID": "span-1",
                    "traceID": "trace-1",
                    "processID": "p1",
                    "operationName": "POST /orders",
                    "startTime": 1000000,
                    "duration": 50000,
                    "tags": [{"key": "http.status_code", "value": "500"}],
                }
            ],
        }
    ]
}


def make_builder(
    *,
    prometheus_result=None,
    loki_result=None,
    tempo_search_result=None,
    tempo_trace_result=None,
    k8s_events_result=None,
    k8s_pods_result=None,
    prometheus=True,
    loki=True,
    tempo=True,
    kubernetes=True,
):
    observation_store = InMemoryObservationStore()
    evidence_store = InMemoryEvidenceStore()
    incident_store = InMemoryIncidentStore()

    prom_client = None
    if prometheus:
        prom_client = PrometheusClient("http://prometheus.test")
        prom_client.query_range = AsyncMock(
            return_value=prometheus_result
            or AdapterResult(status=SourceStatus.AVAILABLE, data=PROM_RANGE_DATA)
        )

    loki_client = None
    if loki:
        loki_client = LokiClient("http://loki.test")
        loki_client.query_range = AsyncMock(
            return_value=loki_result
            or AdapterResult(status=SourceStatus.AVAILABLE, data=LOKI_STREAMS_DATA)
        )

    tempo_client = None
    if tempo:
        tempo_client = TempoClient("http://tempo.test")
        tempo_client.search = AsyncMock(
            return_value=tempo_search_result
            or AdapterResult(status=SourceStatus.AVAILABLE, data=TEMPO_SEARCH_DATA)
        )
        tempo_client.get_trace = AsyncMock(
            return_value=tempo_trace_result
            or AdapterResult(status=SourceStatus.AVAILABLE, data=TEMPO_TRACE_DATA)
        )

    k8s_client = None
    if kubernetes:
        k8s_client = KubernetesClient.__new__(KubernetesClient)
        k8s_client.list_events = AsyncMock(
            return_value=k8s_events_result
            or AdapterResult(status=SourceStatus.AVAILABLE, data=[])
        )
        k8s_client.list_pods = AsyncMock(
            return_value=k8s_pods_result or AdapterResult(status=SourceStatus.AVAILABLE, data=[])
        )

    builder = IncidentContextBuilder(
        prometheus=prom_client,
        loki=loki_client,
        tempo=tempo_client,
        kubernetes=k8s_client,
        observation_store=observation_store,
        evidence_store=evidence_store,
        incident_store=incident_store,
    )
    return builder, observation_store, evidence_store, incident_store


def test_build_collects_all_sources_and_marks_ready():
    builder, obs_store, evid_store, incident_store = make_builder()
    incident = make_incident()
    run(incident_store.save(incident))

    result = run(builder.build(incident))

    statuses = {s.source: s.status for s in result.source_statuses}
    assert statuses["prometheus"] == SourceStatus.AVAILABLE
    assert statuses["loki"] == SourceStatus.AVAILABLE
    assert statuses["tempo"] == SourceStatus.AVAILABLE
    assert statuses["kubernetes"] == SourceStatus.AVAILABLE
    assert len(result.observation_ids) > 0
    assert len(result.evidence_ids) > 0

    final = run(incident_store.get(incident.incident_id))
    assert final.current_phase == IncidentPhase.READY_FOR_INVESTIGATION


def test_build_every_evidence_cites_a_real_observation():
    builder, obs_store, evid_store, incident_store = make_builder()
    incident = make_incident()
    run(incident_store.save(incident))
    run(builder.build(incident))

    evidence_list = run(evid_store.list_by_incident(incident.incident_id))
    observation_ids = {o.observation_id for o in run(obs_store.list_all())}
    assert evidence_list
    for evidence in evidence_list:
        assert evidence.observation_id in observation_ids
        assert evidence.incident_id == incident.incident_id


def test_prometheus_unavailable_does_not_block_other_sources():
    builder, obs_store, evid_store, incident_store = make_builder(
        prometheus_result=AdapterResult(status=SourceStatus.UNAVAILABLE, error="connection refused")
    )
    incident = make_incident()
    run(incident_store.save(incident))
    result = run(builder.build(incident))

    statuses = {s.source: s.status for s in result.source_statuses}
    assert statuses["prometheus"] == SourceStatus.UNAVAILABLE
    assert statuses["loki"] == SourceStatus.AVAILABLE
    assert statuses["tempo"] == SourceStatus.AVAILABLE

    final = run(incident_store.get(incident.incident_id))
    assert final.current_phase == IncidentPhase.READY_FOR_INVESTIGATION


def test_tempo_timeout_reported_without_raising():
    builder, obs_store, evid_store, incident_store = make_builder(
        tempo_search_result=AdapterResult(status=SourceStatus.TIMEOUT, error="Tempo request timed out")
    )
    incident = make_incident()
    run(incident_store.save(incident))
    result = run(builder.build(incident))

    statuses = {s.source: s.status for s in result.source_statuses}
    assert statuses["tempo"] == SourceStatus.TIMEOUT


def test_missing_kubernetes_client_reports_unavailable_not_crash():
    builder, obs_store, evid_store, incident_store = make_builder(kubernetes=False)
    incident = make_incident()
    run(incident_store.save(incident))
    result = run(builder.build(incident))

    statuses = {s.source: s.status for s in result.source_statuses}
    assert statuses["kubernetes"] == SourceStatus.UNAVAILABLE

    final = run(incident_store.get(incident.incident_id))
    assert final.current_phase == IncidentPhase.READY_FOR_INVESTIGATION


def test_all_sources_unavailable_still_reaches_ready_for_investigation():
    builder, obs_store, evid_store, incident_store = make_builder(
        prometheus=False, loki=False, tempo=False, kubernetes=False
    )
    incident = make_incident()
    run(incident_store.save(incident))
    result = run(builder.build(incident))

    statuses = {s.source: s.status for s in result.source_statuses}
    del statuses["alertmanager"]  # always AVAILABLE — it's reading already-stored observations, not a live client
    assert all(status == SourceStatus.UNAVAILABLE for status in statuses.values())
    final = run(incident_store.get(incident.incident_id))
    assert final.current_phase == IncidentPhase.READY_FOR_INVESTIGATION


def test_initial_alerts_produce_evidence_for_already_linked_observations():
    builder, obs_store, evid_store, incident_store = make_builder()
    incident = make_incident()
    run(incident_store.save(incident))

    from shared.models import Correlation, Observation, ObservationSource, SignalType

    alert_obs = Observation.new(
        source=ObservationSource.ALERTMANAGER,
        signal_type=SignalType.ALERT,
        cluster="cloudmart-k3s",
        signal="HighHTTPErrorRate",
        service="order-service",
        correlation=Correlation(incident_id=incident.incident_id),
    )
    run(obs_store.save(alert_obs))

    run(builder.build(incident))

    evidence_list = run(evid_store.list_by_incident(incident.incident_id))
    alert_evidence = [e for e in evidence_list if e.observation_id == alert_obs.observation_id]
    assert len(alert_evidence) == 1
    assert alert_evidence[0].type.value == "alert"


def test_running_build_twice_does_not_duplicate_alert_evidence():
    builder, obs_store, evid_store, incident_store = make_builder()
    incident = make_incident()
    run(incident_store.save(incident))

    from shared.models import Correlation, Observation, ObservationSource, SignalType

    alert_obs = Observation.new(
        source=ObservationSource.ALERTMANAGER,
        signal_type=SignalType.ALERT,
        cluster="cloudmart-k3s",
        signal="HighHTTPErrorRate",
        service="order-service",
        correlation=Correlation(incident_id=incident.incident_id),
    )
    run(obs_store.save(alert_obs))

    run(builder.build(incident))
    run(builder.build(incident))

    evidence_list = run(evid_store.list_by_incident(incident.incident_id))
    alert_evidence = [e for e in evidence_list if e.observation_id == alert_obs.observation_id]
    assert len(alert_evidence) == 1


def test_no_namespace_or_services_skips_heavy_queries_without_crashing():
    builder, obs_store, evid_store, incident_store = make_builder()
    incident = make_incident(affected_namespace=None, affected_services=[])
    run(incident_store.save(incident))

    result = run(builder.build(incident))

    statuses = {s.source: s.status for s in result.source_statuses}
    assert statuses["prometheus"] == SourceStatus.AVAILABLE
    assert statuses["loki"] == SourceStatus.AVAILABLE
    assert statuses["tempo"] == SourceStatus.AVAILABLE
    assert statuses["kubernetes"] == SourceStatus.AVAILABLE
    # nothing to query without namespace/service, so nothing collected
    prom_count = next(s.observation_count for s in result.source_statuses if s.source == "prometheus")
    assert prom_count == 0
