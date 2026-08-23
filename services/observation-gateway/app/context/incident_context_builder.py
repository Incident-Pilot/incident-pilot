"""
Incident Context Builder — spec section 9.

On incident creation/merge, pulls a configurable time window (default
T-15min to now, `settings.context_window_minutes`) of: the incident's
already-linked alerts, recent metrics, recent logs, recent traces, K8s
events, and pod status — normalizes each into canonical Observations, and
records an Evidence entry (with provenance: the actual query/reference
used) for every one of them, so a future RCA agent can cite `ev-001`
instead of inventing a claim.

Deployment status/info (spec section 9) is collected too, as of step 12:
for each affected service, the most recent Deployment record (commit SHA/
branch/deployed_at, read from Kubernetes Deployment annotations via
app/deployment/deployment_context_collector.py) becomes an Observation +
Evidence citing how long before the incident it happened — e.g. "deployed
4 minutes before this incident" (spec section 15's illustrative example).
That's a plain time-delta fact, not a causal claim; nothing here decides
whether the deployment caused anything. Service topology (section 10) is
NOT collected here — that's step 11's own `GET /topology`, not scoped to
an incident. Nothing in this module reasons about root cause; it only
collects, normalizes, and cites.

Resilience (spec section 13): each source's `AdapterResult.status` is
recorded independently. One backend being unavailable/timing out never
stops the others from being collected — this is exactly the "Tempo is
mid-restart-loop" scenario the spec calls out. `build()` persists the
resulting `source_statuses` via `SourceStatusStore` before returning (see
`GET /incidents/{id}/source-status` in app/api/incidents.py) — the
background task in app/api/webhooks.py discards `build()`'s return value,
so this is the only durable record of which sources actually succeeded.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import uuid4

from app.collectors.base import SourceCollectionStatus, SourceStatus
from app.collectors.kubernetes_adapter import KubernetesClient
from app.collectors.loki_adapter import LokiClient
from app.collectors.prometheus_adapter import PrometheusClient
from app.collectors.tempo_adapter import TempoClient
from app.config.settings import settings
from app.deployment.deployment_context_collector import DeploymentContextCollector
from app.normalizers.deployment_normalizer import normalize_deployment
from app.normalizers.kubernetes_normalizer import normalize_events, normalize_pod_statuses
from app.normalizers.loki_normalizer import normalize_log_entries
from app.normalizers.prometheus_normalizer import normalize_metric_series
from app.normalizers.tempo_normalizer import normalize_error_spans
from app.storage.interfaces import (
    DeploymentStore,
    EvidenceStore,
    IncidentStore,
    ObservationStore,
    SourceStatusStore,
)
from app.storage.memory import InMemorySourceStatusStore
from shared.models import (
    Evidence,
    EvidenceType,
    Incident,
    IncidentPhase,
    Observation,
    ObservationSource,
    RawReference,
)

# Query selection (spec section 12's "later concern", decided here — the
# adapters themselves stay generic). These are the standard cAdvisor /
# kube-state-metrics / Traefik metric names kube-prometheus-stack exports
# by default. Live verification status per probe (2026-08-20, see
# docs/LIVE_CLUSTER_VERIFICATION.md and docs/PROGRESS.md):
#   - pod_restarts (kube_pod_container_status_restarts_total): CONFIRMED —
#     real per-pod data returned for namespace="cloudmart-prod".
#   - http_error_rate (traefik_service_requests_total): metric name
#     CONFIRMED to exist on this Prometheus; not yet confirmed to return
#     non-empty data for cloudmart-prod traffic specifically.
#   - cpu_usage_seconds / memory_working_set_bytes: NOT yet run against
#     the live cluster — still exactly the "unverified assumption" this
#     comment originally described. If a probe comes back empty against a
#     service known to be running and generating load, the metric/label
#     name here is wrong for this cluster; nothing else depends on the
#     exact string, so fixing it is a one-line change.
_METRIC_PROBES = {
    "pod_restarts": 'kube_pod_container_status_restarts_total{{namespace="{namespace}",pod=~"{service}.*"}}',
    "cpu_usage_seconds": 'rate(container_cpu_usage_seconds_total{{namespace="{namespace}",pod=~"{service}.*"}}[5m])',
    "memory_working_set_bytes": 'container_memory_working_set_bytes{{namespace="{namespace}",pod=~"{service}.*"}}',
    "http_error_rate": 'sum(rate(traefik_service_requests_total{{service=~".*{service}.*",code=~"5.."}}[5m]))',
}
_LOG_ERROR_QUERY_TEMPLATE = '{{namespace="{namespace}"}} |~ "(?i)error|exception|timeout|fail"'
_MAX_LOG_ENTRIES_PER_SERVICE = 50
_MAX_TRACES_FETCHED_PER_SERVICE = 5


@dataclass
class IncidentContextResult:
    incident_id: str
    window_start: datetime
    window_end: datetime
    source_statuses: List[SourceCollectionStatus] = field(default_factory=list)
    observation_ids: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)


def _linked(observation: Observation, incident_id: str) -> Observation:
    return observation.model_copy(
        update={
            "correlation": observation.correlation.model_copy(
                update={"incident_id": incident_id}
            )
        }
    )


def _deployment_summary_text(deployment, incident: Incident) -> str:
    """Matches spec section 15's illustrative evidence text
    ("order-service deployed 4 minutes before incident") — a plain time
    delta, not a claim about whether the deployment caused anything."""
    delta_seconds = (incident.created_at - deployment.deployed_at).total_seconds()
    minutes = int(delta_seconds // 60)
    sha_suffix = f" (commit {deployment.commit_sha[:7]})" if deployment.commit_sha else ""

    if delta_seconds < 0:
        return f"{deployment.service}'s most recent deployment is after this incident was created{sha_suffix}"
    if minutes < 1:
        return f"{deployment.service} was deployed less than a minute before this incident{sha_suffix}"
    unit = "minute" if minutes == 1 else "minutes"
    return f"{deployment.service} deployed {minutes} {unit} before this incident{sha_suffix}"


def _evidence_for(
    observation: Observation,
    *,
    evidence_type: EvidenceType,
    summary: str,
    query: Optional[str] = None,
) -> Evidence:
    return Evidence(
        evidence_id=f"ev-{uuid4().hex[:12]}",
        incident_id=observation.correlation.incident_id,
        type=evidence_type,
        source=observation.source,
        timestamp=observation.timestamp,
        service=observation.service,
        resource=observation.resource,
        summary=summary,
        observation_id=observation.observation_id,
        raw_reference=RawReference(query=query, trace_id=observation.correlation.trace_id),
    )


class IncidentContextBuilder:
    def __init__(
        self,
        *,
        prometheus: Optional[PrometheusClient],
        loki: Optional[LokiClient],
        tempo: Optional[TempoClient],
        kubernetes: Optional[KubernetesClient],
        observation_store: ObservationStore,
        evidence_store: EvidenceStore,
        incident_store: IncidentStore,
        deployment_store: DeploymentStore,
        source_status_store: Optional[SourceStatusStore] = None,
        window_minutes: Optional[float] = None,
    ):
        self._prometheus = prometheus
        self._loki = loki
        self._tempo = tempo
        self._kubernetes = kubernetes
        self._observation_store = observation_store
        self._evidence_store = evidence_store
        self._incident_store = incident_store
        self._source_status_store = source_status_store or InMemorySourceStatusStore()
        self._deployment_collector = DeploymentContextCollector(
            kubernetes=kubernetes, deployment_store=deployment_store
        )
        self._window_minutes = (
            window_minutes if window_minutes is not None else settings.context_window_minutes
        )

    async def build(self, incident: Incident) -> IncidentContextResult:
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(minutes=self._window_minutes)
        result = IncidentContextResult(
            incident_id=incident.incident_id, window_start=window_start, window_end=now
        )

        collecting = incident.model_copy(
            update={"current_phase": IncidentPhase.COLLECTING_CONTEXT, "updated_at": now}
        )
        await self._incident_store.save(collecting)

        await self._collect_initial_alerts(incident, result)
        await self._collect_metrics(incident, window_start, now, result)
        await self._collect_logs(incident, window_start, now, result)
        await self._collect_traces(incident, window_start, now, result)
        await self._collect_kubernetes(incident, result)
        await self._collect_deployment_context(incident, result)

        await self._source_status_store.save_many(incident.incident_id, result.source_statuses)

        latest = await self._incident_store.get(incident.incident_id)
        finished = (latest or collecting).model_copy(
            update={
                "current_phase": IncidentPhase.READY_FOR_INVESTIGATION,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        await self._incident_store.save(finished)

        return result

    # --- initial alerts (already-normalized Observations from the webhook) --

    async def _collect_initial_alerts(
        self, incident: Incident, result: IncidentContextResult
    ) -> None:
        linked_observations = await self._observation_store.list_by_incident(
            incident.incident_id
        )
        existing_evidence = await self._evidence_store.list_by_incident(incident.incident_id)
        already_cited = {e.observation_id for e in existing_evidence if e.observation_id}

        count = 0
        for obs in linked_observations:
            if obs.source != ObservationSource.ALERTMANAGER or obs.observation_id in already_cited:
                continue
            evidence = _evidence_for(
                obs,
                evidence_type=EvidenceType.ALERT,
                summary=f"Alert '{obs.signal}' fired on {obs.service or 'unknown service'}",
            )
            await self._evidence_store.save(evidence)
            result.evidence_ids.append(evidence.evidence_id)
            count += 1

        result.source_statuses.append(
            SourceCollectionStatus(
                source="alertmanager", status=SourceStatus.AVAILABLE, observation_count=count
            )
        )

    # --- metrics -------------------------------------------------------------

    async def _collect_metrics(
        self, incident: Incident, window_start: datetime, now: datetime, result: IncidentContextResult
    ) -> None:
        if self._prometheus is None:
            result.source_statuses.append(
                SourceCollectionStatus(
                    source="prometheus", status=SourceStatus.UNAVAILABLE, error="client not configured"
                )
            )
            return

        namespace = incident.affected_namespace
        services = incident.affected_services or [None]
        overall_status = SourceStatus.AVAILABLE
        last_error = None
        count = 0

        for service in services:
            for probe_name, template in _METRIC_PROBES.items():
                if namespace is None or service is None:
                    continue
                promql = template.format(namespace=namespace, service=service)
                adapter_result = await self._prometheus.query_range(promql, window_start, now)

                if not adapter_result.ok:
                    overall_status = adapter_result.status
                    last_error = adapter_result.error
                    continue

                observations = normalize_metric_series(
                    adapter_result.data,
                    signal=probe_name,
                    cluster=settings.cluster_name,
                    namespace=namespace,
                    service=service,
                )
                for obs in observations:
                    linked = _linked(obs, incident.incident_id)
                    await self._observation_store.save(linked)
                    result.observation_ids.append(linked.observation_id)
                    evidence = _evidence_for(
                        linked,
                        evidence_type=EvidenceType.METRIC,
                        summary=f"{probe_name} for {service}: {linked.value}",
                        query=promql,
                    )
                    await self._evidence_store.save(evidence)
                    result.evidence_ids.append(evidence.evidence_id)
                    count += 1

        result.source_statuses.append(
            SourceCollectionStatus(
                source="prometheus", status=overall_status, error=last_error, observation_count=count
            )
        )

    # --- logs ------------------------------------------------------------------

    async def _collect_logs(
        self, incident: Incident, window_start: datetime, now: datetime, result: IncidentContextResult
    ) -> None:
        if self._loki is None:
            result.source_statuses.append(
                SourceCollectionStatus(
                    source="loki", status=SourceStatus.UNAVAILABLE, error="client not configured"
                )
            )
            return

        namespace = incident.affected_namespace
        if namespace is None:
            result.source_statuses.append(
                SourceCollectionStatus(
                    source="loki", status=SourceStatus.AVAILABLE, observation_count=0
                )
            )
            return

        logql = _LOG_ERROR_QUERY_TEMPLATE.format(namespace=namespace)
        adapter_result = await self._loki.query_range(
            logql, window_start, now, limit=_MAX_LOG_ENTRIES_PER_SERVICE
        )

        if not adapter_result.ok:
            result.source_statuses.append(
                SourceCollectionStatus(
                    source="loki", status=adapter_result.status, error=adapter_result.error
                )
            )
            return

        entries = self._loki.parse_entries(adapter_result.data)
        services = set(incident.affected_services or [])
        if services:
            entries = [e for e in entries if e.service in services] or entries

        observations = normalize_log_entries(
            entries, cluster=settings.cluster_name, max_entries=_MAX_LOG_ENTRIES_PER_SERVICE
        )
        count = 0
        for obs in observations:
            linked = _linked(obs, incident.incident_id)
            await self._observation_store.save(linked)
            result.observation_ids.append(linked.observation_id)
            evidence = _evidence_for(
                linked,
                evidence_type=EvidenceType.LOG,
                summary=(linked.metadata.get("message") or "")[:200],
                query=logql,
            )
            await self._evidence_store.save(evidence)
            result.evidence_ids.append(evidence.evidence_id)
            count += 1

        result.source_statuses.append(
            SourceCollectionStatus(source="loki", status=SourceStatus.AVAILABLE, observation_count=count)
        )

    # --- traces ------------------------------------------------------------------

    async def _collect_traces(
        self, incident: Incident, window_start: datetime, now: datetime, result: IncidentContextResult
    ) -> None:
        if self._tempo is None:
            result.source_statuses.append(
                SourceCollectionStatus(
                    source="tempo", status=SourceStatus.UNAVAILABLE, error="client not configured"
                )
            )
            return

        namespace = incident.affected_namespace
        services = incident.affected_services
        if not services:
            result.source_statuses.append(
                SourceCollectionStatus(source="tempo", status=SourceStatus.AVAILABLE, observation_count=0)
            )
            return

        overall_status = SourceStatus.AVAILABLE
        last_error = None
        count = 0

        for service in services:
            search_result = await self._tempo.search(
                {
                    "tags": f"service.name={service}",
                    "start": int(window_start.timestamp()),
                    "end": int(now.timestamp()),
                }
            )
            if not search_result.ok:
                overall_status = search_result.status
                last_error = search_result.error
                continue

            summaries = self._tempo.parse_search_results(search_result.data)
            for summary in summaries[:_MAX_TRACES_FETCHED_PER_SERVICE]:
                trace_result = await self._tempo.get_trace(summary.trace_id)
                if not trace_result.ok:
                    continue

                spans = self._tempo.parse_spans(trace_result.data)
                observations = normalize_error_spans(
                    spans, cluster=settings.cluster_name, namespace=namespace
                )
                for obs in observations:
                    linked = _linked(obs, incident.incident_id)
                    await self._observation_store.save(linked)
                    result.observation_ids.append(linked.observation_id)
                    evidence = _evidence_for(
                        linked,
                        evidence_type=EvidenceType.TRACE,
                        summary=f"Error span in trace {summary.trace_id} ({linked.metadata.get('operation')})",
                        query=f"trace_id={summary.trace_id}",
                    )
                    await self._evidence_store.save(evidence)
                    result.evidence_ids.append(evidence.evidence_id)
                    count += 1

        result.source_statuses.append(
            SourceCollectionStatus(
                source="tempo", status=overall_status, error=last_error, observation_count=count
            )
        )

    # --- kubernetes (events + pod status) -----------------------------------------

    async def _collect_kubernetes(self, incident: Incident, result: IncidentContextResult) -> None:
        if self._kubernetes is None:
            result.source_statuses.append(
                SourceCollectionStatus(
                    source="kubernetes", status=SourceStatus.UNAVAILABLE, error="client not configured"
                )
            )
            return

        namespace = incident.affected_namespace
        if namespace is None:
            result.source_statuses.append(
                SourceCollectionStatus(source="kubernetes", status=SourceStatus.AVAILABLE, observation_count=0)
            )
            return

        count = 0
        statuses: List[SourceStatus] = []
        errors: List[str] = []

        events_result = await self._kubernetes.list_events(namespace)
        statuses.append(events_result.status)
        if events_result.ok:
            observations = normalize_events(events_result.data, cluster=settings.cluster_name)
            for obs in observations:
                if obs.timestamp < datetime.now(timezone.utc) - timedelta(
                    minutes=self._window_minutes
                ):
                    continue
                linked = _linked(obs, incident.incident_id)
                await self._observation_store.save(linked)
                result.observation_ids.append(linked.observation_id)
                evidence = _evidence_for(
                    linked,
                    evidence_type=EvidenceType.KUBERNETES_EVENT,
                    summary=f"{linked.signal} on {linked.resource}: {linked.metadata.get('message')}",
                    query=f"list_namespaced_event(namespace={namespace})",
                )
                await self._evidence_store.save(evidence)
                result.evidence_ids.append(evidence.evidence_id)
                count += 1
        elif events_result.error:
            errors.append(events_result.error)

        pods_result = await self._kubernetes.list_pods(namespace)
        statuses.append(pods_result.status)
        if pods_result.ok:
            services = set(incident.affected_services or [])
            pods = [p for p in pods_result.data if not services or any(p.name.startswith(s) for s in services)]
            observations = normalize_pod_statuses(pods, cluster=settings.cluster_name)
            for obs in observations:
                linked = _linked(obs, incident.incident_id)
                await self._observation_store.save(linked)
                result.observation_ids.append(linked.observation_id)
                evidence = _evidence_for(
                    linked,
                    evidence_type=EvidenceType.KUBERNETES_EVENT,
                    summary=f"Pod {linked.resource} status: phase={linked.metadata.get('phase')}, ready={linked.metadata.get('ready')}",
                    query=f"list_namespaced_pod(namespace={namespace})",
                )
                await self._evidence_store.save(evidence)
                result.evidence_ids.append(evidence.evidence_id)
                count += 1
        elif pods_result.error:
            errors.append(pods_result.error)

        overall = SourceStatus.AVAILABLE if all(s == SourceStatus.AVAILABLE for s in statuses) else next(
            (s for s in statuses if s != SourceStatus.AVAILABLE), SourceStatus.AVAILABLE
        )
        result.source_statuses.append(
            SourceCollectionStatus(
                source="kubernetes",
                status=overall,
                error="; ".join(errors) or None,
                observation_count=count,
            )
        )

    # --- deployment context (step 12) ---------------------------------------------

    async def _collect_deployment_context(
        self, incident: Incident, result: IncidentContextResult
    ) -> None:
        namespace = incident.affected_namespace
        if namespace is None or not incident.affected_services:
            result.source_statuses.append(
                SourceCollectionStatus(
                    source="deployment", status=SourceStatus.AVAILABLE, observation_count=0
                )
            )
            return

        # `build()` can run more than once for the same incident (a later
        # webhook delivery correlating into an already-OPEN incident
        # re-triggers context collection — see _collect_initial_alerts'
        # `already_cited` guard for the same reason). Deployment state
        # rarely changes between two closely-spaced runs, so without this
        # guard re-running produces a second, near-identical Evidence entry
        # per service instead of a no-op.
        already_collected = {
            obs.metadata.get("deployment_id")
            for obs in await self._observation_store.list_by_incident(incident.incident_id)
            if obs.source == ObservationSource.GIT and obs.metadata.get("deployment_id")
        }

        overall_status = SourceStatus.AVAILABLE
        last_error = None
        count = 0

        for service in incident.affected_services:
            deployment, status = await self._deployment_collector.collect(namespace, service)
            if status != SourceStatus.AVAILABLE:
                overall_status = status
                continue
            if deployment is None:
                continue
            if deployment.deployment_id in already_collected:
                continue
            already_collected.add(deployment.deployment_id)

            obs = normalize_deployment(
                deployment, cluster=settings.cluster_name, incident_id=incident.incident_id
            )
            await self._observation_store.save(obs)
            result.observation_ids.append(obs.observation_id)

            evidence = _evidence_for(
                obs,
                evidence_type=EvidenceType.DEPLOYMENT,
                summary=_deployment_summary_text(deployment, incident),
                query=f"get_deployment(namespace={namespace}, name={service})",
            )
            await self._evidence_store.save(evidence)
            result.evidence_ids.append(evidence.evidence_id)
            count += 1

        result.source_statuses.append(
            SourceCollectionStatus(
                source="deployment", status=overall_status, error=last_error, observation_count=count
            )
        )
