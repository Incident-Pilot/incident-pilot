"""
POST /webhooks/alertmanager — spec section 5.

Validates the Alertmanager webhook payload (Pydantic model rejects a
malformed body with 422 automatically), normalizes every alert into a
canonical Observation, and — for firing alerts only — creates an Incident.

Incident creation here is deliberately naive: one Incident per webhook
delivery, grouping every *firing* alert in that single payload. This is
NOT the deterministic dedup/correlation described in spec section 7
(namespace/service/resource/time-window based merging of alerts arriving
across multiple separate webhook deliveries) — that is step 9's job and
requires querying already-stored incidents, which this step intentionally
does not do yet. Resolved alerts are normalized and stored as Observations
but do not open or touch an Incident (no correlation lookup exists yet to
know which incident they'd resolve).
"""

from datetime import datetime, timezone
from typing import List
from uuid import uuid4

from fastapi import APIRouter, Depends

from app.api.deps import get_incident_store, get_observation_store
from app.config.settings import settings
from app.models.alertmanager import AlertmanagerWebhookPayload
from app.normalizers.alertmanager_normalizer import normalize_alert
from app.storage.interfaces import IncidentStore, ObservationStore
from shared.models import Incident, Observation, Severity

router = APIRouter()

_SEVERITY_RANK = {
    Severity.CRITICAL: 3,
    Severity.WARNING: 2,
    Severity.INFO: 1,
    Severity.UNKNOWN: 0,
}


def _highest_severity(observations: List[Observation]) -> Severity:
    return max(
        (obs.severity for obs in observations),
        key=lambda s: _SEVERITY_RANK[s],
        default=Severity.UNKNOWN,
    )


def _build_incident(
    firing_observations: List[Observation], payload: AlertmanagerWebhookPayload
) -> Incident:
    now = datetime.now(timezone.utc)
    alertnames: List[str] = []
    services: List[str] = []
    for obs in firing_observations:
        if obs.signal not in alertnames:
            alertnames.append(obs.signal)
        if obs.service and obs.service not in services:
            services.append(obs.service)

    namespace = payload.groupLabels.get("namespace") or next(
        (obs.namespace for obs in firing_observations if obs.namespace), None
    )
    title = payload.groupLabels.get("alertname") or alertnames[0]

    return Incident(
        incident_id=f"INC-{uuid4().hex[:8].upper()}",
        title=title,
        severity=_highest_severity(firing_observations),
        created_at=now,
        updated_at=now,
        source="alertmanager",
        affected_services=services,
        affected_namespace=namespace,
        initial_alerts=alertnames,
    )


@router.post("/webhooks/alertmanager", status_code=202)
async def receive_alertmanager_webhook(
    payload: AlertmanagerWebhookPayload,
    observation_store: ObservationStore = Depends(get_observation_store),
    incident_store: IncidentStore = Depends(get_incident_store),
):
    observations: List[Observation] = [
        normalize_alert(alert, cluster=settings.cluster_name, common_labels=payload.commonLabels)
        for alert in payload.alerts
    ]

    firing = [
        obs
        for obs, alert in zip(observations, payload.alerts)
        if alert.status == "firing"
    ]

    incident = None
    if firing:
        incident = _build_incident(firing, payload)
        firing_ids = {obs.observation_id for obs in firing}
        observations = [
            obs.model_copy(
                update={
                    "correlation": obs.correlation.model_copy(
                        update={"incident_id": incident.incident_id}
                    )
                }
            )
            if obs.observation_id in firing_ids
            else obs
            for obs in observations
        ]
        await incident_store.save(incident)

    for obs in observations:
        await observation_store.save(obs)

    return {
        "status": "accepted",
        "observations_created": [obs.observation_id for obs in observations],
        "incident": (
            {
                "incident_id": incident.incident_id,
                "severity": incident.severity.value,
                "affected_services": incident.affected_services,
                "initial_alerts": incident.initial_alerts,
            }
            if incident
            else None
        ),
    }
