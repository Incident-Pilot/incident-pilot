"""
POST /webhooks/alertmanager — spec section 5.

Validates the Alertmanager webhook payload (Pydantic model rejects a
malformed body with 422 automatically), normalizes every alert into a
canonical Observation, and — for firing alerts only — correlates them
into an Incident via app/correlation/incident_correlator.py (spec section
7's deterministic namespace/service/time-window rule, added in step 9;
before that, this handler always created a new Incident per delivery).
Resolved alerts are normalized and stored as Observations but do not
open, merge into, or resolve an Incident — closing out an incident when
its alerts resolve is lifecycle management, not correlation, and isn't
built yet.

Step 10: once an incident exists for this delivery, context collection
(spec section 9 — metrics/logs/traces/K8s events) is kicked off via
FastAPI's BackgroundTasks so it runs *after* the 202 response is sent —
the webhook caller (Alertmanager) doesn't wait on slow Loki/Tempo/K8s
calls, and a Context Builder failure can't turn into a webhook timeout.
"""

from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends

from app.api.deps import get_context_builder, get_incident_store, get_observation_store
from app.config.settings import settings
from app.context.incident_context_builder import IncidentContextBuilder
from app.correlation.incident_correlator import correlate_or_create_incident
from app.models.alertmanager import AlertmanagerWebhookPayload
from app.normalizers.alertmanager_normalizer import normalize_alert
from app.storage.interfaces import IncidentStore, ObservationStore
from shared.models import Incident, Observation

router = APIRouter()


async def _run_context_builder(context_builder: IncidentContextBuilder, incident: Incident) -> None:
    await context_builder.build(incident)


@router.post("/webhooks/alertmanager", status_code=202)
async def receive_alertmanager_webhook(
    payload: AlertmanagerWebhookPayload,
    background_tasks: BackgroundTasks,
    observation_store: ObservationStore = Depends(get_observation_store),
    incident_store: IncidentStore = Depends(get_incident_store),
    context_builder: IncidentContextBuilder = Depends(get_context_builder),
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
        incident = await correlate_or_create_incident(firing, payload, incident_store)
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

    for obs in observations:
        await observation_store.save(obs)

    if incident is not None:
        background_tasks.add_task(_run_context_builder, context_builder, incident)

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
