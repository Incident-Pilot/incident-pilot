"""
Alertmanager alert -> canonical Observation normalization — spec section 5.

Structural normalization only (label extraction, timestamp parsing, severity
mapping via a fixed label vocabulary). No reasoning about root cause, no
incident correlation here — correlation/dedup is deterministic-rules logic
that belongs to step 9, not this module (spec section 6/29 boundary: this
service collects and normalizes, it does not investigate).
"""

from datetime import datetime, timezone
from typing import Dict, Optional

from shared.models import Correlation, Observation, ObservationSource, Severity, SignalType

_SEVERITY_LABEL_MAP = {
    "critical": Severity.CRITICAL,
    "warning": Severity.WARNING,
    "info": Severity.INFO,
    "none": Severity.INFO,
}

# Prioritized candidate label keys for each semantic field, same "don't
# assume one label convention" discipline used in the Loki adapter (spec
# section 13 note in docs/PROGRESS.md Task 3) — PrometheusRule authors are
# not guaranteed to use the same label name for "which service".
_SERVICE_LABEL_CANDIDATES = ("service", "app", "job", "deployment")
_RESOURCE_LABEL_CANDIDATES = ("pod", "instance", "container")
_NAMESPACE_LABEL_CANDIDATES = ("namespace", "kubernetes_namespace")


def _first_present(labels: Dict[str, str], candidates: tuple) -> Optional[str]:
    for key in candidates:
        value = labels.get(key)
        if value:
            return value
    return None


def _map_severity(labels: Dict[str, str]) -> Severity:
    raw = (labels.get("severity") or "").strip().lower()
    return _SEVERITY_LABEL_MAP.get(raw, Severity.UNKNOWN)


def _parse_timestamp(raw: Optional[str]) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        # Alertmanager emits RFC3339 with a trailing "Z"; datetime.fromisoformat
        # only accepts "+00:00" prior to Python 3.11, so normalize it.
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def normalize_alert(
    alert,  # AlertmanagerAlert
    cluster: str,
    common_labels: Optional[Dict[str, str]] = None,
) -> Observation:
    """One Alertmanager alert -> one canonical Observation.

    Merges commonLabels (webhook-group-level) under the alert's own labels
    so per-alert labels win on conflict, matching Alertmanager's own
    semantics (commonLabels is just the intersection across the group).
    """
    labels: Dict[str, str] = {**(common_labels or {}), **alert.labels}
    alertname = labels.get("alertname", "unknown_alert")
    timestamp = _parse_timestamp(alert.startsAt if alert.status == "firing" else alert.endsAt)

    return Observation.new(
        source=ObservationSource.ALERTMANAGER,
        signal_type=SignalType.ALERT,
        severity=_map_severity(labels),
        cluster=cluster,
        namespace=_first_present(labels, _NAMESPACE_LABEL_CANDIDATES),
        service=_first_present(labels, _SERVICE_LABEL_CANDIDATES),
        resource=_first_present(labels, _RESOURCE_LABEL_CANDIDATES),
        signal=alertname,
        labels=labels,
        metadata={
            "alert_status": alert.status,
            "annotations": alert.annotations,
            "fingerprint": alert.fingerprint,
            "generatorURL": alert.generatorURL,
            "startsAt": alert.startsAt,
            "endsAt": alert.endsAt,
        },
        correlation=Correlation(),
        timestamp=timestamp,
    )
