"""
Deployment context collector — spec section 5.

Reads the annotations deploy.sh stamps on each Deployment plus
Kubernetes' own `deployment.kubernetes.io/revision` annotation, and turns
them into a canonical Deployment record.

`_COMMIT_SHA_ANNOTATION` is the plain (non-namespaced) `commit-sha` key —
confirmed 2026-08-24 against the ecommerce-cloudmart repo's actual
deploy.sh, which runs `kubectl annotate deployment/${svc} ...
commit-sha="${COMMIT_SHA}"`. An earlier version of this file expected the
namespaced `incidentpilot.io/commit-sha` instead, which deploy.sh never
wrote — every deployment lookup silently got `commit_sha=None`. Fixed
here on the reader side rather than re-annotating deploy.sh, since the
already-deployed plain key is what's live on the cluster today.

`_BRANCH_ANNOTATION`/`_DEPLOYED_AT_ANNOTATION` stay namespaced
(`incidentpilot.io/...`) because deploy.sh doesn't stamp branch or
deployed-at under *any* key yet — that's a separate gap in deploy.sh
itself (only the commit-sha annotate call exists), not a naming mismatch
to fix here. Until deploy.sh adds those two annotate calls, `branch` stays
None and `deployed_at` falls back to the Deployment's Kubernetes
`created_at` (see `_parse_deployed_at` below) rather than raising.

`success` is derived from the Deployment's *current* live replica counts
(ready >= desired, none unavailable), not from anything deploy.sh stamps —
a deploy's real outcome isn't known until the rollout finishes, so
re-checking current status is more accurate than trusting a flag set at
kubectl-apply time (which is also unnecessary to add on the app side —
this info is a plain read of state Kubernetes already tracks).
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

from app.collectors.base import SourceStatus
from app.collectors.kubernetes_adapter import DeploymentSummary, KubernetesClient
from app.storage.interfaces import DeploymentStore
from shared.models import Deployment

_COMMIT_SHA_ANNOTATION = "commit-sha"
_BRANCH_ANNOTATION = "incidentpilot.io/branch"
_DEPLOYED_AT_ANNOTATION = "incidentpilot.io/deployed-at"
_ROLLOUT_REVISION_ANNOTATION = "deployment.kubernetes.io/revision"  # k8s's own, not ours


def _parse_deployed_at(raw: Optional[str], fallback: Optional[datetime]) -> datetime:
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return fallback or datetime.now(timezone.utc)


def _derive_success(summary: DeploymentSummary) -> Optional[bool]:
    if summary.replicas is None or summary.ready_replicas is None:
        return None
    return summary.ready_replicas >= summary.replicas and not summary.unavailable_replicas


def deployment_from_summary(summary: DeploymentSummary) -> Deployment:
    annotations = summary.annotations or {}
    commit_sha = annotations.get(_COMMIT_SHA_ANNOTATION)
    deployed_at = _parse_deployed_at(annotations.get(_DEPLOYED_AT_ANNOTATION), summary.created_at)
    deployment_id = f"dep-{summary.name}-{commit_sha or deployed_at.strftime('%Y%m%d%H%M%S')}"

    return Deployment(
        deployment_id=deployment_id,
        service=summary.name,
        namespace=summary.namespace,
        commit_sha=commit_sha,
        branch=annotations.get(_BRANCH_ANNOTATION),
        image_tag=summary.image,
        rollout_revision=annotations.get(_ROLLOUT_REVISION_ANNOTATION),
        deployed_at=deployed_at,
        success=_derive_success(summary),
    )


class DeploymentContextCollector:
    def __init__(
        self, *, kubernetes: Optional[KubernetesClient], deployment_store: DeploymentStore
    ):
        self._kubernetes = kubernetes
        self._deployment_store = deployment_store

    async def collect(
        self, namespace: str, service: str
    ) -> Tuple[Optional[Deployment], SourceStatus]:
        """Fetch, normalize, and persist the current Deployment record for
        one service. Returns (None, status) rather than raising when
        unreachable or when the service has no Deployment at all — callers
        distinguish the two via the returned SourceStatus."""
        if self._kubernetes is None:
            return None, SourceStatus.UNAVAILABLE

        result = await self._kubernetes.get_deployment(namespace, service)
        if not result.ok or result.data is None:
            return None, result.status

        deployment = deployment_from_summary(result.data)
        await self._deployment_store.save(deployment)
        return deployment, SourceStatus.AVAILABLE
