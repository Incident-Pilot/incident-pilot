"""
Live smoke test — run this against your real Prometheus (not mocks).

Usage (from the EC2 box, or your Mac if you port-forward from there):

    kubectl port-forward -n observability svc/kube-prom-kube-prometheus-prometheus 9090:9090 &
    cd services/observation-gateway
    PYTHONPATH=. python scripts/live_check_prometheus.py

Prints the raw AdapterResult for a trivial `up` query. If Prometheus is
reachable and query-able, status should print AVAILABLE with at least
one time series in the result.
"""

import asyncio

from app.collectors.prometheus_adapter import PrometheusClient


async def main() -> None:
    client = PrometheusClient(base_url="http://localhost:9090")
    result = await client.query("up")

    print(f"status: {result.status.value}")
    if result.error:
        print(f"error: {result.error}")
    if result.data:
        series = result.data.get("result", [])
        print(f"series returned: {len(series)}")
        if series:
            print(f"first series metric labels: {series[0].get('metric')}")


if __name__ == "__main__":
    asyncio.run(main())
