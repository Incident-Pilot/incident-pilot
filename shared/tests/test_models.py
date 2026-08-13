from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.models import (
    Correlation,
    Evidence,
    EvidenceType,
    Incident,
    IncidentPhase,
    IncidentStatus,
    Observation,
    ObservationSource,
    Severity,
    SignalType,
)

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------


def test_observation_valid_construction():
    obs = Observation(
        observation_id="obs-001",
        timestamp=NOW,
        source=ObservationSource.PROMETHEUS,
        signal_type=SignalType.METRIC,
        severity=Severity.WARNING,
        cluster="cloudmart-k3s",
        namespace="cloudmart-prod",
        service="order-service",
        resource="order-service-abc123",
        signal="http_error_rate",
        value=0.42,
    )
    assert obs.correlation.trace_id is None
    assert obs.labels == {}
    assert obs.metadata == {}


def test_observation_rejects_naive_timestamp():
    with pytest.raises(ValidationError):
        Observation(
            observation_id="obs-002",
            timestamp=datetime(2026, 8, 13, 9, 30, 0),  # no tzinfo
            source=ObservationSource.LOKI,
            signal_type=SignalType.LOG,
            cluster="cloudmart-k3s",
            signal="db_connection_timeout",
        )


def test_observation_rejects_unknown_enum_value():
    with pytest.raises(ValidationError):
        Observation(
            observation_id="obs-003",
            timestamp=NOW,
            source="not-a-real-source",
            signal_type=SignalType.METRIC,
            cluster="cloudmart-k3s",
            signal="cpu_usage",
        )


def test_observation_rejects_blank_signal():
    with pytest.raises(ValidationError):
        Observation(
            observation_id="obs-004",
            timestamp=NOW,
            source=ObservationSource.PROMETHEUS,
            signal_type=SignalType.METRIC,
            cluster="cloudmart-k3s",
            signal="   ",
        )


def test_observation_new_helper_generates_id_and_timestamp():
    obs = Observation.new(
        source=ObservationSource.KUBERNETES,
        signal_type=SignalType.KUBERNETES_EVENT,
        cluster="cloudmart-k3s",
        signal="CrashLoopBackOff",
        severity=Severity.CRITICAL,
        namespace="cloudmart-prod",
        resource="order-service-abc123",
    )
    assert obs.observation_id.startswith("obs-kubernetes-")
    assert obs.timestamp.tzinfo is not None


def test_observation_json_roundtrip():
    obs = Observation.new(
        source=ObservationSource.TEMPO,
        signal_type=SignalType.TRACE,
        cluster="cloudmart-k3s",
        signal="span_error",
        correlation=Correlation(trace_id="abc123"),
    )
    dumped = obs.model_dump_json()
    restored = Observation.model_validate_json(dumped)
    assert restored == obs


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------


def test_incident_valid_construction():
    inc = Incident(
        incident_id="INC-0001",
        title="Elevated HTTP 500s on order-service",
        severity=Severity.CRITICAL,
        created_at=NOW,
        updated_at=NOW,
        source="alertmanager",
        affected_services=["order-service"],
        affected_namespace="cloudmart-prod",
        initial_alerts=["HighHTTPErrorRate"],
    )
    assert inc.status == IncidentStatus.OPEN
    assert inc.current_phase == IncidentPhase.DETECTED
    assert inc.root_cause is None


def test_incident_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        Incident(
            incident_id="INC-0002",
            title="x",
            severity=Severity.WARNING,
            created_at=NOW,
            updated_at=NOW,
            source="alertmanager",
            root_cause_confidence=1.5,
        )


def test_incident_rejects_updated_before_created():
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later = datetime(2026, 2, 1, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        Incident(
            incident_id="INC-0003",
            title="x",
            severity=Severity.WARNING,
            created_at=later,
            updated_at=earlier,
            source="alertmanager",
        )


def test_incident_rejects_naive_timestamps():
    with pytest.raises(ValidationError):
        Incident(
            incident_id="INC-0004",
            title="x",
            severity=Severity.WARNING,
            created_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 1),
            source="alertmanager",
        )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_evidence_valid_construction():
    ev = Evidence(
        evidence_id="ev-001",
        incident_id="INC-0001",
        type=EvidenceType.LOG,
        source=ObservationSource.LOKI,
        timestamp=NOW,
        service="order-service",
        summary="Database connection timeout",
        observation_id="obs-loki-abc123",
    )
    assert ev.raw_reference.query is None


def test_evidence_rejects_blank_summary():
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="ev-002",
            incident_id="INC-0001",
            type=EvidenceType.METRIC,
            source=ObservationSource.PROMETHEUS,
            timestamp=NOW,
            summary="   ",
        )


def test_evidence_json_roundtrip():
    ev = Evidence(
        evidence_id="ev-003",
        incident_id="INC-0001",
        type=EvidenceType.DEPLOYMENT,
        source=ObservationSource.GIT,
        timestamp=NOW,
        service="order-service",
        summary="order-service deployed 4 minutes before incident",
    )
    dumped = ev.model_dump_json()
    restored = Evidence.model_validate_json(dumped)
    assert restored == ev
