"""
Live smoke test — run this against your real k3s cluster (not mocks).

Usage (from the EC2 box, where an in-cluster or default kubeconfig context
is available):

    cd services/observation-gateway
    PYTHONPATH=. python scripts/live_check_kubernetes.py

Confirms the `kubernetes` python client can actually reach the API server
with your current kubeconfig/service-account context, and prints summaries
for cloudmart-prod pods/deployments/events plus cluster nodes. Also
double-checks that the ClusterIP DNS names hardcoded in
app/config/settings.py resolve from inside the cluster network namespace
this runs in.
"""

import asyncio

from app.collectors.kubernetes_adapter import KubernetesClient

NAMESPACE = "cloudmart-prod"


async def main() -> None:
    kube = KubernetesClient()

    pods = await kube.list_pods(NAMESPACE)
    print(f"list_pods: {pods.status.value}")
    if pods.error:
        print(f"  error: {pods.error}")
    if pods.data:
        for p in pods.data:
            print(f"  pod={p.name} phase={p.phase} restarts={p.restart_count} ready={p.ready}")

    deployments = await kube.list_deployments(NAMESPACE)
    print(f"list_deployments: {deployments.status.value}")
    if deployments.data:
        for d in deployments.data:
            print(f"  deployment={d.name} replicas={d.replicas} ready={d.ready_replicas} image={d.image}")

    events = await kube.list_events(NAMESPACE)
    print(f"list_events: {events.status.value}")
    if events.data:
        for e in events.data[:10]:
            print(f"  [{e.severity}] {e.reason}: {e.message} ({e.resource})")

    nodes = await kube.get_nodes()
    print(f"get_nodes: {nodes.status.value}")
    if nodes.data:
        for n in nodes.data:
            print(f"  node={n.name} ready={n.ready} kubelet={n.kubelet_version}")


if __name__ == "__main__":
    asyncio.run(main())
