"""
GET /incidents, GET /incidents/{id}, GET /incidents/{id}/observations,
GET /incidents/{id}/evidence, GET /incidents/{id}/timeline — spec
section 12.

`GET /incidents/{id}` returns a composite view matching spec section 15's
illustrative incident shape: the incident's own fields, plus a compact
`observations` (ID list) and `evidence` (summarized) so the response is
useful standalone, plus a `topology` subgraph limited to the incident's
`affected_services`. The full Observation/Evidence objects live in their
own dedicated sub-resource endpoints, not duplicated into every incident
response.

`topology` is read from the `TopologyStore` (a fast local read — no live
K8s/Tempo calls), so it reflects whatever `GET /topology` last built, not
a fresh computation per incident. Empty until `GET /topology` has been
called at least once.

No reasoning here either: this layer assembles and serves what's already
been collected/normalized/persisted upstream. It doesn't rank evidence,
summarize an incident, or suggest anything.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import require_api_key
from app.api.deps import (
    get_evidence_store,
    get_incident_store,
    get_observation_store,
    get_source_status_store,
    get_topology_store,
)
from app.storage.interfaces import (
    EvidenceStore,
    IncidentStore,
    ObservationStore,
    SourceStatusStore,
    TopologyStore,
)
from shared.models import Evidence, Incident, Observation

router = APIRouter(dependencies=[Depends(require_api_key)])


async def _get_incident_or_404(incident_id: str, incident_store: IncidentStore) -> Incident:
    incident = await incident_store.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    return incident


@router.get("/incidents")
async def list_incidents(
    status: Optional[str] = None,
    incident_store: IncidentStore = Depends(get_incident_store),
):
    incidents = await incident_store.list_all()
    if status is not None:
        incidents = [i for i in incidents if i.status.value == status]
    incidents.sort(key=lambda i: i.created_at, reverse=True)
    return {"incidents": incidents}


@router.get("/incidents/{incident_id}")
async def get_incident(
    incident_id: str,
    incident_store: IncidentStore = Depends(get_incident_store),
    observation_store: ObservationStore = Depends(get_observation_store),
    evidence_store: EvidenceStore = Depends(get_evidence_store),
    topology_store: TopologyStore = Depends(get_topology_store),
):
    incident = await _get_incident_or_404(incident_id, incident_store)
    observations = await observation_store.list_by_incident(incident_id)
    evidence = await evidence_store.list_by_incident(incident_id)
    full_topology = await topology_store.get_all()

    subgraph = {
        service: full_topology[service]
        for service in incident.affected_services
        if service in full_topology
    }

    return {
        **incident.model_dump(mode="json"),
        "observations": [o.observation_id for o in observations],
        "evidence": [
            {"id": e.evidence_id, "type": e.type.value, "summary": e.summary}
            for e in evidence
        ],
        "topology": subgraph,
    }


@router.get("/incidents/{incident_id}/observations")
async def get_incident_observations(
    incident_id: str,
    incident_store: IncidentStore = Depends(get_incident_store),
    observation_store: ObservationStore = Depends(get_observation_store),
) -> List[Observation]:
    await _get_incident_or_404(incident_id, incident_store)
    return await observation_store.list_by_incident(incident_id)


@router.get("/incidents/{incident_id}/evidence")
async def get_incident_evidence(
    incident_id: str,
    incident_store: IncidentStore = Depends(get_incident_store),
    evidence_store: EvidenceStore = Depends(get_evidence_store),
) -> List[Evidence]:
    await _get_incident_or_404(incident_id, incident_store)
    return await evidence_store.list_by_incident(incident_id)


@router.get("/incidents/{incident_id}/source-status")
async def get_incident_source_status(
    incident_id: str,
    incident_store: IncidentStore = Depends(get_incident_store),
    source_status_store: SourceStatusStore = Depends(get_source_status_store),
):
    """Per-source AVAILABLE/UNAVAILABLE/TIMEOUT/PARTIAL outcome of the
    Incident Context Builder's most recent run for this incident (spec
    section 13/37) — e.g. whether Loki/Tempo/Kubernetes actually
    succeeded, returned empty, or failed, rather than that being visible
    only by reading source code or logs."""
    await _get_incident_or_404(incident_id, incident_store)
    statuses = await source_status_store.list_by_incident(incident_id)
    return {
        "incident_id": incident_id,
        "source_status": [
            {
                "source": s.source,
                "status": s.status.value,
                "error": s.error,
                "observation_count": s.observation_count,
            }
            for s in statuses
        ],
    }


@router.get("/incidents/{incident_id}/timeline")
async def get_incident_timeline(
    incident_id: str,
    incident_store: IncidentStore = Depends(get_incident_store),
    observation_store: ObservationStore = Depends(get_observation_store),
    evidence_store: EvidenceStore = Depends(get_evidence_store),
):
    await _get_incident_or_404(incident_id, incident_store)
    observations = await observation_store.list_by_incident(incident_id)
    evidence = await evidence_store.list_by_incident(incident_id)

    entries = [
        {
            "timestamp": o.timestamp.isoformat(),
            "kind": "observation",
            "id": o.observation_id,
            "source": o.source.value,
            "signal": o.signal,
        }
        for o in observations
    ] + [
        {
            "timestamp": e.timestamp.isoformat(),
            "kind": "evidence",
            "id": e.evidence_id,
            "type": e.type.value,
            "summary": e.summary,
        }
        for e in evidence
    ]
    entries.sort(key=lambda entry: entry["timestamp"])

    return {"incident_id": incident_id, "timeline": entries}
