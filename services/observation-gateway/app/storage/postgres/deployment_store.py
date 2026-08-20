"""PostgresDeploymentStore — satisfies the DeploymentStore Protocol
(app/storage/interfaces.py) against the real `deployments` table."""

from typing import Optional

import asyncpg

from shared.models import Deployment


def row_to_deployment(row) -> Deployment:
    return Deployment(
        deployment_id=row["deployment_id"],
        service=row["service"],
        namespace=row["namespace"],
        commit_sha=row["commit_sha"],
        branch=row["branch"],
        image_tag=row["image_tag"],
        rollout_revision=row["rollout_revision"],
        deployed_at=row["deployed_at"],
        success=row["success"],
        metadata=row["metadata"] or {},
    )


class PostgresDeploymentStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save(self, deployment: Deployment) -> None:
        await self._pool.execute(
            """
            INSERT INTO deployments (
                deployment_id, service, namespace, commit_sha, branch,
                image_tag, rollout_revision, deployed_at, success, metadata
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (deployment_id) DO UPDATE SET
                commit_sha = EXCLUDED.commit_sha,
                branch = EXCLUDED.branch,
                image_tag = EXCLUDED.image_tag,
                rollout_revision = EXCLUDED.rollout_revision,
                deployed_at = EXCLUDED.deployed_at,
                success = EXCLUDED.success,
                metadata = EXCLUDED.metadata
            """,
            deployment.deployment_id,
            deployment.service,
            deployment.namespace,
            deployment.commit_sha,
            deployment.branch,
            deployment.image_tag,
            deployment.rollout_revision,
            deployment.deployed_at,
            deployment.success,
            deployment.metadata,
        )

    async def get_latest(self, service: str) -> Optional[Deployment]:
        row = await self._pool.fetchrow(
            "SELECT * FROM deployments WHERE service = $1 ORDER BY deployed_at DESC LIMIT 1",
            service,
        )
        return row_to_deployment(row) if row else None
