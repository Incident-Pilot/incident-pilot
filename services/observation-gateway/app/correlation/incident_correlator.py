"""
Deterministic incident correlation — spec section 7.

"Multiple related alerts (HTTP 500 spike + latency spike + pod restarts)
should usually correlate into one incident, not three." The rule used here
is namespace + at least one overlapping service + a fixed time window
(settings.correlation_window_minutes) against already-stored OPEN
incidents — no AI/ML, no fuzzy matching, per the spec's explicit
constraint. This replaces the naive "one incident per webhook delivery"
behavior from step 7: that logic only grouped alerts arriving in the same
HTTP request; this looks across separate deliveries too, now that there's
a real store (step 8) to query.

Deliberately does NOT touch incident resolution/closure — a firing alert
either creates or merges into an OPEN incident; nothing here ever changes
`status`. Resolved alerts aren't passed to this module at all (the webhook
handler only calls it with firing observations).
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import uuid4

from app.config.settings import settings
from app.models.alertmanager import AlertmanagerWebhookPayload
from app.storage.interfaces import IncidentStore
from shared.models import Incident, Observation, Severity

_SEVERITY_RANK = {
    Severity.CRITICAL: 3,
    Severity.WARNING: 2,
    Severity.INFO: 1,
    Severity.UNKNOWN: 0,
}


def _higher_severity(a: Severity, b: Severity) -> Severity:
    return a if _SEVERITY_RANK[a] >= _SEVERITY_RANK[b] else b


def _batch_severity(observations: List[Observation]) -> Severity:
    severity = Severity.UNKNOWN
    for obs in observations:
        severity = _higher_severity(severity, obs.severity)
    return severity


def _derive_namespace(
    observations: List[Observation], payload: AlertmanagerWebhookPayload
) -> Optional[str]:
    return payload.groupLabels.get("namespace") or next(
        (obs.namespace for obs in observations if obs.namespace), None
    )


def _derive_services(observations: List[Observation]) -> List[str]:
    services: List[str] = []
    for obs in observations:
        if obs.service and obs.service not in services:
            services.append(obs.service)
    return services


def _derive_alertnames(observations: List[Observation]) -> List[str]:
    alertnames: List[str] = []
    for obs in observations:
        if obs.signal not in alertnames:
            alertnames.append(obs.signal)
    return alertnames


def _union(existing: List[str], new: List[str]) -> List[str]:
    merged = list(existing)
    for item in new:
        if item not in merged:
            merged.append(item)
    return merged


def _new_incident(
    firing_observations: List[Observation],
    payload: AlertmanagerWebhookPayload,
    namespace: Optional[str],
    services: List[str],
    alertnames: List[str],
) -> Incident:
    now = datetime.now(timezone.utc)
    title = payload.groupLabels.get("alertname") or alertnames[0]

    return Incident(
        incident_id=f"INC-{uuid4().hex[:8].upper()}",
        title=title,
        severity=_batch_severity(firing_observations),
        created_at=now,
        updated_at=now,
        source="alertmanager",
        affected_services=services,
        affected_namespace=namespace,
        initial_alerts=alertnames,
    )


def _merge_into(
    target: Incident,
    firing_observations: List[Observation],
    services: List[str],
    alertnames: List[str],
) -> Incident:
    return target.model_copy(
        update={
            "affected_services": _union(target.affected_services, services),
            "initial_alerts": _union(target.initial_alerts, alertnames),
            "severity": _higher_severity(target.severity, _batch_severity(firing_observations)),
            "updated_at": datetime.now(timezone.utc),
        }
    )


async def correlate_or_create_incident(
    firing_observations: List[Observation],
    payload: AlertmanagerWebhookPayload,
    incident_store: IncidentStore,
) -> Incident:
    """Firing alerts from one webhook delivery -> the Incident they belong
    to, merging into an existing OPEN incident when the deterministic
    rule matches and creating a new one otherwise. Always persists the
    result via `incident_store.save()` before returning."""

    namespace = _derive_namespace(firing_observations, payload)
    services = _derive_services(firing_observations)
    alertnames = _derive_alertnames(firing_observations)

    since = datetime.now(timezone.utc) - timedelta(
        minutes=settings.correlation_window_minutes
    )
    candidates = await incident_store.find_correlation_candidates(
        namespace=namespace, services=services, since=since
    )

    if candidates:
        # Deterministic tie-break when more than one OPEN incident matches:
        # the most recently updated one is the most likely still-active fit.
        target = max(candidates, key=lambda incident: incident.updated_at)
        incident = _merge_into(target, firing_observations, services, alertnames)
    else:
        incident = _new_incident(firing_observations, payload, namespace, services, alertnames)

    await incident_store.save(incident)
    return incident
