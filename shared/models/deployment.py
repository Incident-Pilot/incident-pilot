"""
Canonical Deployment model — spec section 5's deployment-context fields
(commit SHA, branch, deployment timestamp, service, image tag, rollout
revision, success/failure).

Populated from Kubernetes Deployment annotations: `incidentpilot.io/
commit-sha`/`branch`/`deployed-at` are stamped by deploy.sh (step 12,
ecommerce-cloudmart repo) since the app's own image tagging is a static
`v1` with no commit info; `rollout_revision` reads Kubernetes' own
`deployment.kubernetes.io/revision` annotation, which needs no change on
the app side. `success` is derived from live replica counts at collection
time, not stamped at deploy time, since a deploy's real outcome isn't
known until the rollout actually finishes.
"""

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Deployment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deployment_id: str
    service: str
    namespace: str

    commit_sha: Optional[str] = None
    branch: Optional[str] = None
    image_tag: Optional[str] = None
    rollout_revision: Optional[str] = None
    deployed_at: datetime
    success: Optional[bool] = None

    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("deployment_id", "service", "namespace")
    @classmethod
    def _must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty/blank")
        return v

    @field_validator("deployed_at")
    @classmethod
    def _must_be_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("deployed_at must be timezone-aware (use UTC)")
        return v
