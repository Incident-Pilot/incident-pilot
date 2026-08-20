"""
POST /ingest/gitleaks, POST /ingest/trivy — spec section 5.

Ingestion only: turns already-generated Gitleaks/Trivy JSON reports
(deploy.sh writes these to reports/*.json on every deploy, per the
ecommerce-cloudmart repo) into Observations. No reasoning about which
findings matter, no remediation, and no opinion on deploy.sh's existing
choice to run both tools non-blocking (`--exit-code 0`) — that choice
isn't revisited here.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.auth import require_api_key
from app.api.deps import get_observation_store
from app.config.settings import settings
from app.models.security_reports import GitleaksReport, TrivyReport
from app.normalizers.gitleaks_normalizer import normalize_gitleaks_findings
from app.normalizers.trivy_normalizer import normalize_trivy_report
from app.storage.interfaces import ObservationStore

router = APIRouter(dependencies=[Depends(require_api_key)])


@router.post("/ingest/gitleaks", status_code=202)
async def ingest_gitleaks_report(
    findings: GitleaksReport,
    observation_store: ObservationStore = Depends(get_observation_store),
):
    observations = normalize_gitleaks_findings(findings, cluster=settings.cluster_name)
    for obs in observations:
        await observation_store.save(obs)

    return {
        "status": "accepted",
        "findings_ingested": len(observations),
        "observations_created": [o.observation_id for o in observations],
    }


@router.post("/ingest/trivy", status_code=202)
async def ingest_trivy_report(
    report: TrivyReport,
    service: Optional[str] = Query(
        default=None,
        description="Overrides the service derived from the report's ArtifactName, "
        "for callers where that parsing might not apply.",
    ),
    observation_store: ObservationStore = Depends(get_observation_store),
):
    observations = normalize_trivy_report(
        report, cluster=settings.cluster_name, service_override=service
    )
    for obs in observations:
        await observation_store.save(obs)

    return {
        "status": "accepted",
        "findings_ingested": len(observations),
        "observations_created": [o.observation_id for o in observations],
    }
