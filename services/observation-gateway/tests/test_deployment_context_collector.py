import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.collectors.base import AdapterResult, SourceStatus
from app.collectors.kubernetes_adapter import DeploymentSummary, KubernetesClient
from app.deployment.deployment_context_collector import DeploymentContextCollector
from app.storage.memory import InMemoryDeploymentStore


def run(coro):
    return asyncio.run(coro)


def make_k8s_client(deployment_result=None):
    client = KubernetesClient.__new__(KubernetesClient)
    client.get_deployment = AsyncMock(
        return_value=deployment_result
        or AdapterResult(
            status=SourceStatus.AVAILABLE,
            data=DeploymentSummary(
                name="order-service",
                namespace="cloudmart-prod",
                replicas=2,
                ready_replicas=2,
                unavailable_replicas=None,
                image="localhost:5000/cloudmart/order-service:v1",
                created_at=datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc),
                annotations={
                    "incidentpilot.io/commit-sha": "abc1234def",
                    "incidentpilot.io/branch": "main",
                    "incidentpilot.io/deployed-at": "2026-08-20T09:00:00Z",
                    "deployment.kubernetes.io/revision": "7",
                },
            ),
        )
    )
    return client


def test_collect_parses_annotations_into_deployment():
    store = InMemoryDeploymentStore()
    collector = DeploymentContextCollector(kubernetes=make_k8s_client(), deployment_store=store)

    deployment, status = run(collector.collect("cloudmart-prod", "order-service"))

    assert status == SourceStatus.AVAILABLE
    assert deployment.commit_sha == "abc1234def"
    assert deployment.branch == "main"
    assert deployment.rollout_revision == "7"
    assert deployment.image_tag == "localhost:5000/cloudmart/order-service:v1"
    assert deployment.deployed_at == datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    assert deployment.success is True


def test_collect_persists_to_store():
    store = InMemoryDeploymentStore()
    collector = DeploymentContextCollector(kubernetes=make_k8s_client(), deployment_store=store)
    run(collector.collect("cloudmart-prod", "order-service"))

    latest = run(store.get_latest("order-service"))
    assert latest is not None
    assert latest.commit_sha == "abc1234def"


def test_collect_falls_back_to_created_at_when_no_deployed_at_annotation():
    k8s = make_k8s_client(
        AdapterResult(
            status=SourceStatus.AVAILABLE,
            data=DeploymentSummary(
                name="order-service",
                namespace="cloudmart-prod",
                created_at=datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc),
                annotations={},
            ),
        )
    )
    store = InMemoryDeploymentStore()
    collector = DeploymentContextCollector(kubernetes=k8s, deployment_store=store)

    deployment, status = run(collector.collect("cloudmart-prod", "order-service"))

    assert status == SourceStatus.AVAILABLE
    assert deployment.commit_sha is None
    assert deployment.deployed_at == datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc)


def test_collect_unreachable_returns_none_and_status():
    k8s = make_k8s_client(
        AdapterResult(status=SourceStatus.TIMEOUT, error="Kubernetes API call timed out")
    )
    store = InMemoryDeploymentStore()
    collector = DeploymentContextCollector(kubernetes=k8s, deployment_store=store)

    deployment, status = run(collector.collect("cloudmart-prod", "order-service"))

    assert deployment is None
    assert status == SourceStatus.TIMEOUT
    assert run(store.get_latest("order-service")) is None


def test_collect_no_kubernetes_client_reports_unavailable():
    store = InMemoryDeploymentStore()
    collector = DeploymentContextCollector(kubernetes=None, deployment_store=store)

    deployment, status = run(collector.collect("cloudmart-prod", "order-service"))

    assert deployment is None
    assert status == SourceStatus.UNAVAILABLE


def test_success_false_when_replicas_not_all_ready():
    k8s = make_k8s_client(
        AdapterResult(
            status=SourceStatus.AVAILABLE,
            data=DeploymentSummary(
                name="order-service",
                namespace="cloudmart-prod",
                replicas=3,
                ready_replicas=1,
                unavailable_replicas=2,
                annotations={},
            ),
        )
    )
    store = InMemoryDeploymentStore()
    collector = DeploymentContextCollector(kubernetes=k8s, deployment_store=store)

    deployment, _ = run(collector.collect("cloudmart-prod", "order-service"))
    assert deployment.success is False


def test_success_none_when_replica_counts_unknown():
    k8s = make_k8s_client(
        AdapterResult(
            status=SourceStatus.AVAILABLE,
            data=DeploymentSummary(name="order-service", namespace="cloudmart-prod", annotations={}),
        )
    )
    store = InMemoryDeploymentStore()
    collector = DeploymentContextCollector(kubernetes=k8s, deployment_store=store)

    deployment, _ = run(collector.collect("cloudmart-prod", "order-service"))
    assert deployment.success is None
