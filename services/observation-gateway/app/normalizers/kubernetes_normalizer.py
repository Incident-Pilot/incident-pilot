"""
K8sEvent / PodSummary (app/collectors/kubernetes_adapter.py's parsed
shapes) -> canonical Observation — spec section 9/16.

This is the normalization Task 5 explicitly deferred: mapping the raw
Kubernetes event `type` ("Normal"/"Warning") onto the canonical Severity
enum. The mapping table below is the whole decision — anything not in it
falls back to UNKNOWN rather than guessing.
"""

from datetime import datetime, timezone
from typing import List

from app.collectors.kubernetes_adapter import K8sEvent, PodSummary
from shared.models import Observation, ObservationSource, Severity, SignalType

_EVENT_TYPE_SEVERITY_MAP = {
    "Normal": Severity.INFO,
    "Warning": Severity.WARNING,
}


def normalize_events(events: List[K8sEvent], *, cluster: str) -> List[Observation]:
    observations: List[Observation] = []
    for event in events:
        observations.append(
            Observation.new(
                source=ObservationSource.KUBERNETES,
                signal_type=SignalType.KUBERNETES_EVENT,
                severity=_EVENT_TYPE_SEVERITY_MAP.get(event.severity or "", Severity.UNKNOWN),
                cluster=cluster,
                namespace=event.namespace,
                resource=event.resource,
                signal=event.reason or "unknown_event",
                labels={},
                metadata={"message": event.message, "count": event.count},
                timestamp=event.timestamp or datetime.now(timezone.utc),
            )
        )
    return observations


def normalize_pod_statuses(pods: List[PodSummary], *, cluster: str) -> List[Observation]:
    """Not-ready or crash-looping pods surface as WARNING; healthy pods
    still get an Observation (spec section 9 wants "pod status" as
    context regardless of whether anything looks wrong) but at INFO."""

    observations: List[Observation] = []
    for pod in pods:
        reasons = [c.reason for c in pod.containers if c.reason]
        severity = Severity.WARNING if (not pod.ready or reasons) else Severity.INFO
        observations.append(
            Observation.new(
                source=ObservationSource.KUBERNETES,
                signal_type=SignalType.KUBERNETES_EVENT,
                severity=severity,
                cluster=cluster,
                namespace=pod.namespace,
                resource=pod.name,
                signal="pod_status",
                labels={},
                metadata={
                    "phase": pod.phase,
                    "ready": pod.ready,
                    "restart_count": pod.restart_count,
                    "reasons": reasons,
                },
                timestamp=pod.created_at or datetime.now(timezone.utc),
            )
        )
    return observations
