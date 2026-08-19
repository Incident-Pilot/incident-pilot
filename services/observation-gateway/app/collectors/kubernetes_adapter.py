"""
Kubernetes adapter — spec sections 15-16.

Read-only by design (spec section 15: "Do NOT implement write operations
in this phase"). Wraps the official `kubernetes` python client, which is
synchronous, in `asyncio.to_thread` so it fits the same async adapter
interface as the Prometheus/Loki/Tempo clients — including the same
never-raises `AdapterResult` resilience pattern (spec section 29).

Secret/ConfigMap handling (spec section 15: "Do NOT retrieve secret
values"): `list_secret_metadata()` intentionally never reads `.data` or
`.string_data` off the fetched V1Secret objects — only metadata fields
are copied into `SecretMetadata`, so secret values never enter this
process's normalized output even though the underlying API call returns
the full object. Same discipline applies to ConfigMap `.data`.

Event normalization (spec section 16): `list_events()` preserves the raw
Kubernetes event `type` ("Normal"/"Warning") as-is rather than mapping it
into the canonical `Severity` enum (critical/warning/info/unknown) — that
mapping is a normalization decision that belongs to the normalizer layer
(step 14), not this adapter (spec section 7: the gateway collects/
normalizes, later stages reason).
"""

import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TypeVar

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from pydantic import BaseModel, ConfigDict

from .base import AdapterResult, SourceStatus

T = TypeVar("T")


class ContainerStatusSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ready: bool
    restart_count: int
    state: str  # "running" | "waiting" | "terminated" | "unknown"
    reason: Optional[str] = None  # e.g. "CrashLoopBackOff", "OOMKilled"


class PodSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str
    phase: Optional[str] = None
    node_name: Optional[str] = None
    pod_ip: Optional[str] = None
    restart_count: int = 0
    ready: bool = False
    containers: List[ContainerStatusSummary] = []
    created_at: Optional[datetime] = None


class DeploymentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str
    replicas: Optional[int] = None
    ready_replicas: Optional[int] = None
    available_replicas: Optional[int] = None
    updated_replicas: Optional[int] = None
    unavailable_replicas: Optional[int] = None
    image: Optional[str] = None
    created_at: Optional[datetime] = None


class ReplicaSetSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str
    replicas: Optional[int] = None
    ready_replicas: Optional[int] = None
    owner_deployment: Optional[str] = None
    created_at: Optional[datetime] = None


class ServiceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str
    type: Optional[str] = None
    cluster_ip: Optional[str] = None
    selector: Dict[str, str] = {}
    ports: List[Dict[str, Any]] = []


class EndpointsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str
    ready_addresses: List[str] = []
    not_ready_addresses: List[str] = []


class NodeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    ready: bool = False
    kubelet_version: Optional[str] = None
    capacity: Dict[str, str] = {}
    allocatable: Dict[str, str] = {}


class NamespaceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    phase: Optional[str] = None


class ConfigMapMetadata(BaseModel):
    """Metadata only — `.data`/`.binary_data` are never read (spec section 15)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str
    labels: Dict[str, str] = {}
    created_at: Optional[datetime] = None


class SecretMetadata(BaseModel):
    """Metadata only — `.data`/`.string_data` are never read (spec section 15)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    namespace: str
    type: Optional[str] = None
    labels: Dict[str, str] = {}
    created_at: Optional[datetime] = None


class K8sEvent(BaseModel):
    """Normalized Kubernetes event (spec section 16). `severity` carries
    the raw event `type` ("Normal"/"Warning"), not a canonical Severity —
    see module docstring."""

    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = None
    message: Optional[str] = None
    resource: Optional[str] = None  # "<Kind>/<name>"
    namespace: Optional[str] = None
    timestamp: Optional[datetime] = None
    severity: Optional[str] = None
    count: Optional[int] = None


def _load_kube_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


class KubernetesClient:
    def __init__(
        self,
        core_v1: Optional["client.CoreV1Api"] = None,
        apps_v1: Optional["client.AppsV1Api"] = None,
        timeout_seconds: float = 5.0,
    ):
        if core_v1 is None or apps_v1 is None:
            _load_kube_config()
        self.core_v1 = core_v1 or client.CoreV1Api()
        self.apps_v1 = apps_v1 or client.AppsV1Api()
        self.timeout_seconds = timeout_seconds

    # --- pods ---------------------------------------------------------

    async def list_pods(self, namespace: str) -> AdapterResult[List[PodSummary]]:
        result = await self._call(self.core_v1.list_namespaced_pod, namespace)
        return self._summarize(result, self._to_pod_summary)

    async def get_pod(self, namespace: str, name: str) -> AdapterResult[PodSummary]:
        result = await self._call(self.core_v1.read_namespaced_pod, name, namespace)
        if not result.ok or result.data is None:
            return result
        return AdapterResult(status=SourceStatus.AVAILABLE, data=self._to_pod_summary(result.data))

    # --- deployments ----------------------------------------------------

    async def list_deployments(self, namespace: str) -> AdapterResult[List[DeploymentSummary]]:
        result = await self._call(self.apps_v1.list_namespaced_deployment, namespace)
        return self._summarize(result, self._to_deployment_summary)

    async def get_deployment(
        self, namespace: str, name: str
    ) -> AdapterResult[DeploymentSummary]:
        result = await self._call(self.apps_v1.read_namespaced_deployment, name, namespace)
        if not result.ok or result.data is None:
            return result
        return AdapterResult(
            status=SourceStatus.AVAILABLE, data=self._to_deployment_summary(result.data)
        )

    # --- replicasets ----------------------------------------------------

    async def list_replicasets(self, namespace: str) -> AdapterResult[List[ReplicaSetSummary]]:
        result = await self._call(self.apps_v1.list_namespaced_replica_set, namespace)
        return self._summarize(result, self._to_replicaset_summary)

    # --- services / endpoints --------------------------------------------

    async def list_services(self, namespace: str) -> AdapterResult[List[ServiceSummary]]:
        result = await self._call(self.core_v1.list_namespaced_service, namespace)
        return self._summarize(result, self._to_service_summary)

    async def list_endpoints(self, namespace: str) -> AdapterResult[List[EndpointsSummary]]:
        result = await self._call(self.core_v1.list_namespaced_endpoints, namespace)
        return self._summarize(result, self._to_endpoints_summary)

    # --- events (spec section 16) ----------------------------------------

    async def list_events(self, namespace: str) -> AdapterResult[List[K8sEvent]]:
        result = await self._call(self.core_v1.list_namespaced_event, namespace)
        return self._summarize(result, self._to_event)

    # --- cluster-scoped ---------------------------------------------------

    async def get_nodes(self) -> AdapterResult[List[NodeSummary]]:
        result = await self._call(self.core_v1.list_node)
        return self._summarize(result, self._to_node_summary)

    async def get_namespaces(self) -> AdapterResult[List[NamespaceSummary]]:
        result = await self._call(self.core_v1.list_namespace)
        return self._summarize(
            result,
            lambda ns: NamespaceSummary(
                name=ns.metadata.name, phase=ns.status.phase if ns.status else None
            ),
        )

    # --- configmaps / secrets (metadata only) -----------------------------

    async def list_configmap_metadata(
        self, namespace: str
    ) -> AdapterResult[List[ConfigMapMetadata]]:
        result = await self._call(self.core_v1.list_namespaced_config_map, namespace)
        return self._summarize(
            result,
            lambda cm: ConfigMapMetadata(
                name=cm.metadata.name,
                namespace=cm.metadata.namespace,
                labels=cm.metadata.labels or {},
                created_at=cm.metadata.creation_timestamp,
            ),
        )

    async def list_secret_metadata(self, namespace: str) -> AdapterResult[List[SecretMetadata]]:
        result = await self._call(self.core_v1.list_namespaced_secret, namespace)
        return self._summarize(
            result,
            lambda s: SecretMetadata(
                name=s.metadata.name,
                namespace=s.metadata.namespace,
                type=s.type,
                labels=s.metadata.labels or {},
                created_at=s.metadata.creation_timestamp,
            ),
        )

    # --- internal ----------------------------------------------------------

    async def _call(self, fn: Callable[..., T], *args: Any) -> AdapterResult[T]:
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(fn, *args), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            return AdapterResult(
                status=SourceStatus.TIMEOUT, error="Kubernetes API call timed out"
            )
        except ApiException as exc:
            return AdapterResult(
                status=SourceStatus.UNAVAILABLE,
                error=f"Kubernetes API returned HTTP {exc.status}: {exc.reason}",
            )
        except Exception as exc:  # noqa: BLE001 - adapter must never raise (spec section 29)
            return AdapterResult(
                status=SourceStatus.UNAVAILABLE,
                error=f"Could not reach Kubernetes API: {exc}",
            )
        return AdapterResult(status=SourceStatus.AVAILABLE, data=data)

    @staticmethod
    def _summarize(
        result: AdapterResult[Any], mapper: Callable[[Any], Any]
    ) -> AdapterResult[List[Any]]:
        if not result.ok or result.data is None:
            return result
        try:
            summarized = [mapper(item) for item in result.data.items]
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(
                status=SourceStatus.UNAVAILABLE,
                error=f"Failed to parse Kubernetes response: {exc}",
            )
        return AdapterResult(status=SourceStatus.AVAILABLE, data=summarized)

    @staticmethod
    def _to_pod_summary(pod: "client.V1Pod") -> PodSummary:
        statuses = (pod.status.container_statuses if pod.status else None) or []
        containers = []
        for cs in statuses:
            if cs.state.running is not None:
                state, reason = "running", None
            elif cs.state.waiting is not None:
                state, reason = "waiting", cs.state.waiting.reason
            elif cs.state.terminated is not None:
                state, reason = "terminated", cs.state.terminated.reason
            else:
                state, reason = "unknown", None
            containers.append(
                ContainerStatusSummary(
                    name=cs.name,
                    ready=cs.ready,
                    restart_count=cs.restart_count,
                    state=state,
                    reason=reason,
                )
            )
        return PodSummary(
            name=pod.metadata.name,
            namespace=pod.metadata.namespace,
            phase=pod.status.phase if pod.status else None,
            node_name=pod.spec.node_name if pod.spec else None,
            pod_ip=pod.status.pod_ip if pod.status else None,
            restart_count=sum(c.restart_count for c in containers),
            ready=bool(containers) and all(c.ready for c in containers),
            containers=containers,
            created_at=pod.metadata.creation_timestamp,
        )

    @staticmethod
    def _to_deployment_summary(dep: "client.V1Deployment") -> DeploymentSummary:
        image = None
        if dep.spec and dep.spec.template.spec.containers:
            image = dep.spec.template.spec.containers[0].image
        status = dep.status
        return DeploymentSummary(
            name=dep.metadata.name,
            namespace=dep.metadata.namespace,
            replicas=status.replicas if status else None,
            ready_replicas=status.ready_replicas if status else None,
            available_replicas=status.available_replicas if status else None,
            updated_replicas=status.updated_replicas if status else None,
            unavailable_replicas=status.unavailable_replicas if status else None,
            image=image,
            created_at=dep.metadata.creation_timestamp,
        )

    @staticmethod
    def _to_replicaset_summary(rs: "client.V1ReplicaSet") -> ReplicaSetSummary:
        owner_deployment = None
        for ref in rs.metadata.owner_references or []:
            if ref.kind == "Deployment":
                owner_deployment = ref.name
                break
        return ReplicaSetSummary(
            name=rs.metadata.name,
            namespace=rs.metadata.namespace,
            replicas=rs.status.replicas if rs.status else None,
            ready_replicas=rs.status.ready_replicas if rs.status else None,
            owner_deployment=owner_deployment,
            created_at=rs.metadata.creation_timestamp,
        )

    @staticmethod
    def _to_service_summary(svc: "client.V1Service") -> ServiceSummary:
        ports = [
            {
                "name": p.name,
                "port": p.port,
                "target_port": str(p.target_port) if p.target_port is not None else None,
                "protocol": p.protocol,
            }
            for p in (svc.spec.ports or [])
        ] if svc.spec else []
        return ServiceSummary(
            name=svc.metadata.name,
            namespace=svc.metadata.namespace,
            type=svc.spec.type if svc.spec else None,
            cluster_ip=svc.spec.cluster_ip if svc.spec else None,
            selector=(svc.spec.selector or {}) if svc.spec else {},
            ports=ports,
        )

    @staticmethod
    def _to_endpoints_summary(ep: "client.V1Endpoints") -> EndpointsSummary:
        ready_addresses: List[str] = []
        not_ready_addresses: List[str] = []
        for subset in ep.subsets or []:
            ready_addresses.extend(a.ip for a in (subset.addresses or []))
            not_ready_addresses.extend(a.ip for a in (subset.not_ready_addresses or []))
        return EndpointsSummary(
            name=ep.metadata.name,
            namespace=ep.metadata.namespace,
            ready_addresses=ready_addresses,
            not_ready_addresses=not_ready_addresses,
        )

    @staticmethod
    def _to_node_summary(node: "client.V1Node") -> NodeSummary:
        ready = any(
            c.type == "Ready" and c.status == "True" for c in (node.status.conditions or [])
        ) if node.status else False
        return NodeSummary(
            name=node.metadata.name,
            ready=ready,
            kubelet_version=(
                node.status.node_info.kubelet_version
                if node.status and node.status.node_info
                else None
            ),
            capacity=(node.status.capacity or {}) if node.status else {},
            allocatable=(node.status.allocatable or {}) if node.status else {},
        )

    @staticmethod
    def _to_event(event: "client.CoreV1Event") -> K8sEvent:
        involved = event.involved_object
        resource = f"{involved.kind}/{involved.name}" if involved else None
        timestamp = event.last_timestamp or event.event_time or event.first_timestamp
        return K8sEvent(
            reason=event.reason,
            message=event.message,
            resource=resource,
            namespace=event.metadata.namespace if event.metadata else involved.namespace if involved else None,
            timestamp=timestamp,
            severity=event.type,
            count=event.count,
        )
