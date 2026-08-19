import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from app.collectors.base import SourceStatus
from app.collectors.kubernetes_adapter import KubernetesClient


def run(coro):
    return asyncio.run(coro)


def make_client(core_v1=None, apps_v1=None, timeout_seconds=5.0) -> KubernetesClient:
    return KubernetesClient(
        core_v1=core_v1 or MagicMock(),
        apps_v1=apps_v1 or MagicMock(),
        timeout_seconds=timeout_seconds,
    )


# --- resilience (spec section 29) -------------------------------------------


def test_api_exception_is_reported_as_unavailable_not_raised():
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.side_effect = ApiException(status=500, reason="Internal Error")

    kube = make_client(core_v1=core_v1)
    result = run(kube.list_pods("cloudmart-prod"))

    assert result.status == SourceStatus.UNAVAILABLE
    assert "500" in result.error


def test_connection_error_is_reported_not_raised():
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.side_effect = ConnectionRefusedError("connection refused")

    kube = make_client(core_v1=core_v1)
    result = run(kube.list_pods("cloudmart-prod"))

    assert result.status == SourceStatus.UNAVAILABLE
    assert "Could not reach Kubernetes API" in result.error


def test_slow_call_times_out_without_raising():
    import time

    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.side_effect = lambda *a, **k: time.sleep(0.2)

    kube = make_client(core_v1=core_v1, timeout_seconds=0.01)
    result = run(kube.list_pods("cloudmart-prod"))

    assert result.status == SourceStatus.TIMEOUT


# --- pods --------------------------------------------------------------


def _make_pod(name="order-service-abc123", restarts=3, ready=True, phase="Running"):
    return client.V1Pod(
        metadata=client.V1ObjectMeta(
            name=name,
            namespace="cloudmart-prod",
            creation_timestamp=datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc),
        ),
        spec=client.V1PodSpec(containers=[], node_name="node-1"),
        status=client.V1PodStatus(
            phase=phase,
            pod_ip="10.0.0.5",
            container_statuses=[
                client.V1ContainerStatus(
                    name="order-service",
                    ready=ready,
                    restart_count=restarts,
                    image="localhost:5000/order-service:latest",
                    image_id="",
                    state=client.V1ContainerState(
                        waiting=client.V1ContainerStateWaiting(reason="CrashLoopBackOff")
                        if not ready
                        else None,
                        running=client.V1ContainerStateRunning() if ready else None,
                    ),
                )
            ],
        ),
    )


def test_list_pods_summarizes_restart_count_and_readiness():
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = client.V1PodList(items=[_make_pod()])

    kube = make_client(core_v1=core_v1)
    result = run(kube.list_pods("cloudmart-prod"))

    assert result.status == SourceStatus.AVAILABLE
    pod = result.data[0]
    assert pod.name == "order-service-abc123"
    assert pod.namespace == "cloudmart-prod"
    assert pod.restart_count == 3
    assert pod.ready is True
    assert pod.containers[0].state == "running"


def test_list_pods_reports_crashloopbackoff_reason():
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = client.V1PodList(
        items=[_make_pod(ready=False, phase="Pending")]
    )

    kube = make_client(core_v1=core_v1)
    result = run(kube.list_pods("cloudmart-prod"))

    pod = result.data[0]
    assert pod.ready is False
    assert pod.containers[0].state == "waiting"
    assert pod.containers[0].reason == "CrashLoopBackOff"


def test_get_pod_returns_single_summary():
    core_v1 = MagicMock()
    core_v1.read_namespaced_pod.return_value = _make_pod()

    kube = make_client(core_v1=core_v1)
    result = run(kube.get_pod("cloudmart-prod", "order-service-abc123"))

    assert result.status == SourceStatus.AVAILABLE
    assert result.data.name == "order-service-abc123"
    core_v1.read_namespaced_pod.assert_called_once_with(
        "order-service-abc123", "cloudmart-prod"
    )


# --- deployments ---------------------------------------------------------


def test_list_deployments_summarizes_replica_counts_and_image():
    apps_v1 = MagicMock()
    apps_v1.list_namespaced_deployment.return_value = client.V1DeploymentList(
        items=[
            client.V1Deployment(
                metadata=client.V1ObjectMeta(
                    name="order-service",
                    namespace="cloudmart-prod",
                    creation_timestamp=datetime(2026, 8, 13, 9, 0, tzinfo=timezone.utc),
                ),
                spec=client.V1DeploymentSpec(
                    selector=client.V1LabelSelector(match_labels={"app": "order-service"}),
                    template=client.V1PodTemplateSpec(
                        spec=client.V1PodSpec(
                            containers=[
                                client.V1Container(
                                    name="order-service",
                                    image="localhost:5000/order-service:v42",
                                )
                            ]
                        )
                    ),
                ),
                status=client.V1DeploymentStatus(
                    replicas=3, ready_replicas=1, available_replicas=1, updated_replicas=3
                ),
            )
        ]
    )

    kube = make_client(apps_v1=apps_v1)
    result = run(kube.list_deployments("cloudmart-prod"))

    dep = result.data[0]
    assert dep.name == "order-service"
    assert dep.replicas == 3
    assert dep.ready_replicas == 1
    assert dep.image == "localhost:5000/order-service:v42"


# --- events (spec section 16) ---------------------------------------------


def test_list_events_preserves_reason_message_resource_and_raw_type():
    core_v1 = MagicMock()
    core_v1.list_namespaced_event.return_value = client.CoreV1EventList(
        items=[
            client.CoreV1Event(
                metadata=client.V1ObjectMeta(name="evt1", namespace="cloudmart-prod"),
                reason="CrashLoopBackOff",
                message="Back-off restarting failed container",
                type="Warning",
                count=5,
                last_timestamp=datetime(2026, 8, 13, 9, 5, tzinfo=timezone.utc),
                involved_object=client.V1ObjectReference(
                    kind="Pod", name="order-service-abc123", namespace="cloudmart-prod"
                ),
            )
        ]
    )

    kube = make_client(core_v1=core_v1)
    result = run(kube.list_events("cloudmart-prod"))

    event = result.data[0]
    assert event.reason == "CrashLoopBackOff"
    assert event.resource == "Pod/order-service-abc123"
    assert event.namespace == "cloudmart-prod"
    assert event.severity == "Warning"  # raw type preserved, not mapped to canonical Severity
    assert event.count == 5
    assert event.timestamp == datetime(2026, 8, 13, 9, 5, tzinfo=timezone.utc)


# --- nodes -----------------------------------------------------------------


def test_get_nodes_derives_ready_from_conditions():
    core_v1 = MagicMock()
    core_v1.list_node.return_value = client.V1NodeList(
        items=[
            client.V1Node(
                metadata=client.V1ObjectMeta(name="ec2-node-1"),
                status=client.V1NodeStatus(
                    conditions=[
                        client.V1NodeCondition(type="MemoryPressure", status="False"),
                        client.V1NodeCondition(type="Ready", status="True"),
                    ],
                    node_info=client.V1NodeSystemInfo(
                        kubelet_version="v1.28.5+k3s1",
                        architecture="amd64",
                        boot_id="",
                        container_runtime_version="",
                        kernel_version="",
                        kube_proxy_version="",
                        machine_id="",
                        operating_system="linux",
                        os_image="",
                        system_uuid="",
                    ),
                    capacity={"cpu": "2", "memory": "4Gi"},
                    allocatable={"cpu": "1900m", "memory": "3.5Gi"},
                ),
            )
        ]
    )

    kube = make_client(core_v1=core_v1)
    result = run(kube.get_nodes())

    node = result.data[0]
    assert node.ready is True
    assert node.kubelet_version == "v1.28.5+k3s1"
    assert node.capacity["memory"] == "4Gi"


# --- secrets/configmaps: metadata-only guarantee (spec section 15) ---------


def test_secret_metadata_never_exposes_data_field():
    core_v1 = MagicMock()
    core_v1.list_namespaced_secret.return_value = client.V1SecretList(
        items=[
            client.V1Secret(
                metadata=client.V1ObjectMeta(
                    name="db-credentials",
                    namespace="cloudmart-prod",
                    labels={"app": "order-service"},
                ),
                type="Opaque",
                data={"password": "c3VwZXJzZWNyZXQ="},  # deliberately sensitive-looking
            )
        ]
    )

    kube = make_client(core_v1=core_v1)
    result = run(kube.list_secret_metadata("cloudmart-prod"))

    secret_meta = result.data[0]
    assert secret_meta.name == "db-credentials"
    assert secret_meta.type == "Opaque"
    # SecretMetadata has no `data`/`string_data` field at all (extra="forbid"
    # at construction time already enforces this), so the value cannot leak
    # through this object however it's later serialized.
    assert not hasattr(secret_meta, "data")
    assert not hasattr(secret_meta, "string_data")


def test_configmap_metadata_never_exposes_data_field():
    core_v1 = MagicMock()
    core_v1.list_namespaced_config_map.return_value = client.V1ConfigMapList(
        items=[
            client.V1ConfigMap(
                metadata=client.V1ObjectMeta(name="app-config", namespace="cloudmart-prod"),
                data={"LOG_LEVEL": "debug"},
            )
        ]
    )

    kube = make_client(core_v1=core_v1)
    result = run(kube.list_configmap_metadata("cloudmart-prod"))

    cm_meta = result.data[0]
    assert cm_meta.name == "app-config"
    assert not hasattr(cm_meta, "data")
