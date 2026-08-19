"""
Live smoke test — run this against your real Loki (not mocks).

Usage (from the EC2 box, or your Mac if you port-forward from there):

    kubectl port-forward -n observability svc/loki 3100:3100 &
    cd services/observation-gateway
    PYTHONPATH=. python scripts/live_check_loki.py

Prints the raw AdapterResult for a trivial query against the
cloudmart-prod namespace, plus the parsed LogEntry fields for the first
few lines. Use this to confirm the real Promtail label names (namespace/
pod/container/service) match what loki_adapter.py's parse_entries()
expects — if `service` or `pod` come back None on real data, the label
candidate lists in loki_adapter.py need updating.
"""

import asyncio

from app.collectors.loki_adapter import LokiClient


async def main() -> None:
    client = LokiClient(base_url="http://localhost:3100")
    result = await client.query('{namespace="cloudmart-prod"}', limit=5)

    print(f"status: {result.status.value}")
    if result.error:
        print(f"error: {result.error}")
    if result.data:
        entries = LokiClient.parse_entries(result.data)
        print(f"entries returned: {len(entries)}")
        for e in entries[:5]:
            print(
                f"  ts={e.timestamp} namespace={e.namespace} pod={e.pod} "
                f"container={e.container} service={e.service} "
                f"labels={e.labels} message={e.message[:80]!r}"
            )


if __name__ == "__main__":
    asyncio.run(main())
