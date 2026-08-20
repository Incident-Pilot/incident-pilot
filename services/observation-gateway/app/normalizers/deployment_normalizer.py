"""Deployment -> canonical Observation — spec section 9.

Structural only: carries the deployment's own fields into an Observation
so it goes through the same Observation-then-Evidence provenance chain as
every other context source, rather than being a special case.
"""

from typing import Optional

from shared.models import Correlation, Deployment, Observation, ObservationSource, SignalType


def normalize_deployment(
    deployment: Deployment, *, cluster: str, incident_id: Optional[str] = None
) -> Observation:
    return Observation.new(
        source=ObservationSource.GIT,
        signal_type=SignalType.DEPLOYMENT_EVENT,
        cluster=cluster,
        namespace=deployment.namespace,
        service=deployment.service,
        signal="deployment",
        labels={},
        metadata={
            "commit_sha": deployment.commit_sha,
            "branch": deployment.branch,
            "image_tag": deployment.image_tag,
            "rollout_revision": deployment.rollout_revision,
            "success": deployment.success,
            "deployment_id": deployment.deployment_id,
        },
        correlation=Correlation(incident_id=incident_id),
        timestamp=deployment.deployed_at,
    )
