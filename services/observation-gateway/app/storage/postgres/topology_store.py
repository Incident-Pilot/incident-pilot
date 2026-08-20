"""PostgresTopologyStore — satisfies the TopologyStore Protocol
(app/storage/interfaces.py) against the real `service_topology` table."""

from datetime import datetime, timezone
from typing import List

import asyncpg

from app.storage.interfaces import TopologyGraph


class PostgresTopologyStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save_service(self, service: str, namespace: str, depends_on: List[str]) -> None:
        await self._pool.execute(
            """
            INSERT INTO service_topology (service, namespace, depends_on, updated_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (service) DO UPDATE SET
                namespace = EXCLUDED.namespace,
                depends_on = EXCLUDED.depends_on,
                updated_at = EXCLUDED.updated_at
            """,
            service,
            namespace,
            depends_on,
            datetime.now(timezone.utc),
        )

    async def get_all(self) -> TopologyGraph:
        rows = await self._pool.fetch("SELECT service, depends_on FROM service_topology")
        return {row["service"]: row["depends_on"] or [] for row in rows}
