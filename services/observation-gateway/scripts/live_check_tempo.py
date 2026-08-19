"""
Live smoke test — run this against your real Tempo (not mocks).

Usage (from the EC2 box, or your Mac if you port-forward from there):

    kubectl port-forward -n observability svc/tempo 3200:3200 &
    cd services/observation-gateway
    # Get a real trace ID first, e.g. from Grafana's Tempo explore view
    # or from an order-service -> product-service call.
    PYTHONPATH=. TRACE_ID=<real-trace-id> python scripts/live_check_tempo.py

Prints the raw AdapterResult plus parsed Span fields. If `service` comes
back None for every span, or parse_spans() returns an empty list despite
`data.data` looking non-empty, the response-shape assumption documented
in tempo_adapter.py (Jaeger-style trace JSON) is wrong for this Tempo
version and needs to switch to OTLP parsing.
"""

import asyncio
import os

from app.collectors.tempo_adapter import TempoClient


async def main() -> None:
    client = TempoClient(base_url="http://localhost:3200")
    trace_id = os.environ.get("TRACE_ID")

    if trace_id:
        result = await client.get_trace(trace_id)
        print(f"get_trace status: {result.status.value}")
        if result.error:
            print(f"error: {result.error}")
        if result.data:
            spans = TempoClient.parse_spans(result.data)
            print(f"spans parsed: {len(spans)}")
            for s in spans[:10]:
                print(
                    f"  span={s.span_id} parent={s.parent_span_id} "
                    f"service={s.service} op={s.operation} "
                    f"duration_ms={s.duration_ms} status={s.status}"
                )
    else:
        print("No TRACE_ID set — running a search instead.")
        result = await client.search({"tags": "service.name=order-service", "limit": 5})
        print(f"search status: {result.status.value}")
        if result.error:
            print(f"error: {result.error}")
        if result.data:
            summaries = TempoClient.parse_search_results(result.data)
            print(f"traces found: {len(summaries)}")
            for s in summaries:
                print(f"  trace_id={s.trace_id} root_service={s.root_service} "
                      f"duration_ms={s.duration_ms}")


if __name__ == "__main__":
    asyncio.run(main())
