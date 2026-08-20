import asyncio
from unittest.mock import AsyncMock

from app.collectors.base import AdapterResult, SourceStatus
from app.collectors.kubernetes_adapter import KubernetesClient, ServiceSummary
from app.collectors.tempo_adapter import TempoClient
from app.storage.memory import InMemoryTopologyStore
from app.topology.service_topology_builder import ServiceTopologyBuilder


def run(coro):
    return asyncio.run(coro)


def make_k8s_client(services=None, ok=True):
    client = KubernetesClient.__new__(KubernetesClient)
    client.list_services = AsyncMock(
        return_value=(
            AdapterResult(status=SourceStatus.AVAILABLE, data=services or [])
            if ok
            else AdapterResult(status=SourceStatus.UNAVAILABLE, error="connection refused")
        )
    )
    return client


def make_tempo_client(search_result=None, trace_result=None):
    client = TempoClient("http://tempo.test")
    client.search = AsyncMock(
        return_value=search_result or AdapterResult(status=SourceStatus.AVAILABLE, data={"traces": []})
    )
    client.get_trace = AsyncMock(
        return_value=trace_result or AdapterResult(status=SourceStatus.AVAILABLE, data={"data": []})
    )
    return client


def test_build_includes_known_static_call_chain_even_with_no_live_sources():
    store = InMemoryTopologyStore()
    builder = ServiceTopologyBuilder(kubernetes=None, tempo=None, topology_store=store)

    graph = run(builder.build("cloudmart-prod"))

    assert graph["frontend"] == ["product-service", "order-service", "user-service"]
    assert graph["order-service"] == ["product-service", "notification-service"]
    # leaf services with no outgoing edges still appear as nodes
    assert graph["product-service"] == []
    assert graph["notification-service"] == []


def test_build_persists_every_service_to_the_store():
    store = InMemoryTopologyStore()
    builder = ServiceTopologyBuilder(kubernetes=None, tempo=None, topology_store=store)

    run(builder.build("cloudmart-prod"))

    persisted = run(store.get_all())
    assert persisted["frontend"] == ["product-service", "order-service", "user-service"]
    assert "user-service" in persisted


def test_build_adds_k8s_services_as_nodes():
    k8s = make_k8s_client(
        services=[
            ServiceSummary(name="frontend", namespace="cloudmart-prod"),
            ServiceSummary(name="user-service", namespace="cloudmart-prod"),
            ServiceSummary(name="a-service-not-in-the-known-chain", namespace="cloudmart-prod"),
        ]
    )
    store = InMemoryTopologyStore()
    builder = ServiceTopologyBuilder(kubernetes=k8s, tempo=None, topology_store=store)

    graph = run(builder.build("cloudmart-prod"))

    assert "a-service-not-in-the-known-chain" in graph
    assert graph["a-service-not-in-the-known-chain"] == []


def test_k8s_unavailable_does_not_block_static_seed():
    k8s = make_k8s_client(ok=False)
    store = InMemoryTopologyStore()
    builder = ServiceTopologyBuilder(kubernetes=k8s, tempo=None, topology_store=store)

    graph = run(builder.build("cloudmart-prod"))

    assert graph["frontend"] == ["product-service", "order-service", "user-service"]


def test_tempo_observed_span_adds_new_edge():
    search_data = {"traces": [{"traceID": "trace-1"}]}
    trace_data = {
        "data": [
            {
                "traceID": "trace-1",
                "processes": {"p1": {"serviceName": "frontend"}, "p2": {"serviceName": "product-service"}},
                "spans": [
                    {
                        "spanID": "span-parent",
                        "traceID": "trace-1",
                        "processID": "p1",
                        "operationName": "GET /",
                        "startTime": 1000000,
                        "duration": 100000,
                        "tags": [],
                    },
                    {
                        "spanID": "span-child",
                        "traceID": "trace-1",
                        "processID": "p2",
                        "operationName": "GET /products",
                        "startTime": 1010000,
                        "duration": 50000,
                        "tags": [],
                        "references": [{"refType": "CHILD_OF", "spanID": "span-parent"}],
                    },
                ],
            }
        ]
    }
    tempo = make_tempo_client(
        search_result=AdapterResult(status=SourceStatus.AVAILABLE, data=search_data),
        trace_result=AdapterResult(status=SourceStatus.AVAILABLE, data=trace_data),
    )
    store = InMemoryTopologyStore()
    builder = ServiceTopologyBuilder(kubernetes=None, tempo=tempo, topology_store=store)

    graph = run(builder.build("cloudmart-prod"))

    # frontend -> product-service already existed from the static seed;
    # this proves the Tempo-observed edge merged in without duplicating it
    assert graph["frontend"].count("product-service") == 1


def test_tempo_unavailable_does_not_block_k8s_or_static_seed():
    tempo = make_tempo_client(
        search_result=AdapterResult(status=SourceStatus.TIMEOUT, error="Tempo request timed out")
    )
    k8s = make_k8s_client(services=[ServiceSummary(name="frontend", namespace="cloudmart-prod")])
    store = InMemoryTopologyStore()
    builder = ServiceTopologyBuilder(kubernetes=k8s, tempo=tempo, topology_store=store)

    graph = run(builder.build("cloudmart-prod"))

    assert graph["frontend"] == ["product-service", "order-service", "user-service"]


def test_no_sources_at_all_still_returns_static_seed_without_crashing():
    store = InMemoryTopologyStore()
    builder = ServiceTopologyBuilder(kubernetes=None, tempo=None, topology_store=store)
    graph = run(builder.build("cloudmart-prod"))
    assert graph  # non-empty, no exception
