"""
Service topology builder — spec section 10.

Combines three sources into one adjacency-list graph, persisted to the
`service_topology` table and served via GET /topology:

  1. The known, spec-documented CloudMart call chain (static seed — this
     is what the application's own code does, not an inference):
     frontend -> product-service/order-service/user-service,
     order-service -> product-service/notification-service.
  2. Every Kubernetes Service in the namespace, so every service appears
     as a node even before any call to/from it has been observed.
  3. Tempo-observed span parent/child relationships — a child span whose
     `service` differs from its parent span's `service` is a real
     observed call, read directly off span data. Not inferred, not
     scored; just parent->child structurally, same discipline as the
     Context Builder's error-span normalization (step 10).

No reasoning happens here: sources are merged (edge lists deduplicated,
unioned), never weighted or filtered by "importance". Partial failure is
tolerated the same way as everywhere else in this service — Kubernetes or
Tempo being unreachable degrades to "topology built from fewer sources",
not an error.
"""

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.collectors.kubernetes_adapter import KubernetesClient
from app.collectors.tempo_adapter import TempoClient
from app.storage.interfaces import TopologyGraph, TopologyStore

_KNOWN_CALL_CHAIN: Dict[str, List[str]] = {
    "frontend": ["product-service", "order-service", "user-service"],
    "order-service": ["product-service", "notification-service"],
}

_MAX_TRACES_PER_SERVICE = 5


def _merge_edge(graph: Dict[str, List[str]], source: str, target: str) -> None:
    if source == target:
        return
    deps = graph.setdefault(source, [])
    if target not in deps:
        deps.append(target)


class ServiceTopologyBuilder:
    def __init__(
        self,
        *,
        kubernetes: Optional[KubernetesClient],
        tempo: Optional[TempoClient],
        topology_store: TopologyStore,
        trace_window_minutes: float = 60.0,
    ):
        self._kubernetes = kubernetes
        self._tempo = tempo
        self._topology_store = topology_store
        self._trace_window_minutes = trace_window_minutes

    async def build(self, namespace: str) -> TopologyGraph:
        graph: Dict[str, List[str]] = {
            service: list(deps) for service, deps in _KNOWN_CALL_CHAIN.items()
        }
        for deps in _KNOWN_CALL_CHAIN.values():
            for svc in deps:
                graph.setdefault(svc, [])

        if self._kubernetes is not None:
            k8s_result = await self._kubernetes.list_services(namespace)
            if k8s_result.ok and k8s_result.data:
                for svc in k8s_result.data:
                    graph.setdefault(svc.name, [])

        if self._tempo is not None:
            now = datetime.now(timezone.utc)
            start = now - timedelta(minutes=self._trace_window_minutes)
            for service in list(graph.keys()):
                search_result = await self._tempo.search(
                    {
                        "tags": f"service.name={service}",
                        "start": int(start.timestamp()),
                        "end": int(now.timestamp()),
                    }
                )
                if not search_result.ok:
                    continue

                summaries = self._tempo.parse_search_results(search_result.data)
                for summary in summaries[:_MAX_TRACES_PER_SERVICE]:
                    trace_result = await self._tempo.get_trace(summary.trace_id)
                    if not trace_result.ok:
                        continue

                    spans = self._tempo.parse_spans(trace_result.data)
                    by_span_id = {s.span_id: s for s in spans}
                    for span in spans:
                        if not span.parent_span_id or not span.service:
                            continue
                        parent = by_span_id.get(span.parent_span_id)
                        if parent and parent.service and parent.service != span.service:
                            _merge_edge(graph, parent.service, span.service)
                            graph.setdefault(span.service, [])

        for service, deps in graph.items():
            await self._topology_store.save_service(service, namespace, deps)

        return graph
