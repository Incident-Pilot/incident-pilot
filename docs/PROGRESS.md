# IncidentPilot — Phase 2A Progress

Tracking against the 25-step method (Phase 2A spec, section 38) and the
completion checklist (section 37). Update this file at the end of every
task so a new session can pick up without re-deriving context.

## Method sequence (spec section 38)

- [x] 1. Inspect existing repository — N/A, fresh scaffold
- [x] 2. Understand Phase 1 deployment — captured from spec doc (Prometheus/Loki/Tempo/Grafana on k3s, verified working)
- [x] 3. Identify Prometheus/Loki/Tempo endpoints — **DONE**, see below
- [x] 4. Define data models — DONE (Task 1)
- [x] 5. Implement Prometheus adapter — **DONE this task**
- [x] 6. Test Prometheus adapter — **DONE this task** (mocked; live check script provided)
- [x] 7. Implement Loki adapter — **DONE this task**
- [x] 8. Test Loki adapter — **DONE this task** (mocked; live check script provided)
- [x] 9. Implement Tempo adapter — **DONE this task**
- [x] 10. Test Tempo adapter — **DONE this task** (mocked; live check script provided)
- [x] 11. Implement Kubernetes adapter — **DONE this task**
- [x] 12. Test Kubernetes adapter — **DONE this task** (mocked; live check script provided)
- [x] 13. Implement Alertmanager webhook — **DONE this task**
- [x] 14. Implement normalization — **DONE this task**
- [ ] 15. Implement PostgreSQL persistence
- [ ] 16. Implement incident correlation
- [ ] 17. Implement context builder
- [ ] 18. Implement topology
- [ ] 19. Implement deployment context
- [ ] 20. Implement security context
- [ ] 21. Implement API
- [ ] 22. Deploy to k3s
- [ ] 23. Run controlled failure
- [ ] 24. Verify complete incident lifecycle
- [ ] 25. Document architecture

## Task log

### Task 1 — Repo scaffold + canonical models (DONE)

- Created `incidentpilot/` repo skeleton (services/observation-gateway,
  shared, infrastructure, docs).
- Implemented canonical `Observation`, `Incident`, `Evidence` Pydantic
  models in `shared/models/` per spec sections 11, 18, 21-22.
- 13 unit tests in `shared/tests/test_models.py`, all passing (validated
  in sandbox with Python 3.12 / pydantic 2.9.2 / pytest 8.3.3).
- **Design decision needing your confirmation**: the spec lists both
  `status` and `current_phase` as Incident fields without fully
  separating their meaning. Implemented as two independent axes:
  `status` (open/resolved/closed) and `current_phase` (the Phase 2A
  DETECTED → ... → READY_FOR_INVESTIGATION state machine). Flag if you
  intended something else.
- Not yet decided: exact Postgres schema / ORM (SQLAlchemy vs raw SQL)
  — deferred to the persistence task (step 15).

### Task 2 — Endpoints identified + Prometheus adapter (DONE)

- Verified real ClusterIP DNS names via `kubectl get svc -n observability -o wide`
  on the CloudMart k3s cluster (2026-08-13):
  - Prometheus: `kube-prom-kube-prometheus-prometheus.observability.svc.cluster.local:9090`
    (note: the *service* is named `kube-prom-kube-prometheus-prometheus`,
    not `prometheus` — a plain `prometheus` service does not exist, this
    is the kube-prometheus-stack Helm chart's default naming)
  - Loki: `loki.observability.svc.cluster.local:3100`
  - Tempo: `tempo.observability.svc.cluster.local:3200` (HTTP query port)
  - **Unconfirmed assumption**: no auth in front of these query APIs.
    `kubectl get secrets -n observability` showed only TLS/webhook certs
    for the Prometheus operator admission webhook and Grafana's admin
    login — nothing indicating basic auth on Prometheus/Loki/Tempo
    themselves. Flagging this as an assumption, not a verified fact.
- Implemented `PrometheusClient` (`app/collectors/prometheus_adapter.py`):
  generic `query()` / `query_range()` wrapping the Prometheus HTTP API.
  Deliberately does NOT hardcode which PromQL matters (spec section 12) —
  metric-name selection (CPU/memory/error-rate/etc.) is deferred until
  real metric names are verified against this cluster.
- Implemented `AdapterResult`/`SourceStatus` (`app/collectors/base.py`) —
  shared resilience wrapper (spec section 29): adapter calls never raise,
  they return AVAILABLE/UNAVAILABLE/TIMEOUT so a Prometheus outage
  degrades to partial incident context.
- 8 unit tests (`services/observation-gateway/tests/test_prometheus_adapter.py`)
  using `httpx.MockTransport` — no live cluster needed. All 13 shared-model
  tests + 8 adapter tests pass together (21 total), verified in sandbox.
- Added `services/observation-gateway/scripts/live_check_prometheus.py` —
  a live smoke test to run against the real cluster via
  `kubectl port-forward` to confirm the client works against actual
  Prometheus, not just mocks. **Not yet run against your cluster — this
  is the next thing to verify before calling Task 2 fully closed.**

### Task 3 — Loki adapter (DONE)

- Implemented `LokiClient` (`app/collectors/loki_adapter.py`): generic
  `query()` / `query_range()` wrapping the Loki HTTP API
  (`/loki/api/v1/query`, `/loki/api/v1/query_range`), same
  never-raises / `AdapterResult` resilience pattern as the Prometheus
  adapter (spec section 29). Does not hardcode which LogQL matters —
  callers (future Incident Context Builder) supply the query.
- Added `LokiClient.parse_entries()`: flattens a raw Loki `streams` result
  into structured `LogEntry` records (timestamp, namespace, pod,
  container, service, labels, message — spec section 13's required
  fields). **Promtail's real label names on this cluster are still
  unverified** (flagged in Task 2 too), so each semantic field is resolved
  from a prioritized list of common label-name candidates
  (`namespace`/`kubernetes_namespace_name`/`k8s_namespace`, etc.) rather
  than assuming one is correct — the full raw label dict is always kept
  on the entry too, so nothing is lost if the real convention differs.
  **Action item for you**: run `scripts/live_check_loki.py` against the
  real cluster and check whether `pod`/`service`/`container` come back
  populated on real data; if not, add the actual label key to the
  candidate list in `loki_adapter.py`.
- 14 unit tests (`services/observation-gateway/tests/test_loki_adapter.py`)
  covering the same failure modes as the Prometheus adapter (timeout,
  5xx, connection error, malformed API response) plus `parse_entries`
  edge cases (multiple streams, multi-line streams, empty/malformed data,
  unparseable timestamps skipped without raising, label-candidate
  fallback). Full suite: 34 tests passing (13 shared-model + 8 Prometheus
  + 13 Loki), verified via
  `pytest shared/tests services/observation-gateway/tests -v`.
- Added `services/observation-gateway/scripts/live_check_loki.py` —
  live smoke test against real Loki via `kubectl port-forward`, prints
  parsed `LogEntry` fields so you can eyeball whether label resolution
  is correct on real data. Not yet run against your cluster.
- No new dependencies — `httpx` + `pydantic` (already in
  `requirements.txt`) cover this adapter.

### Task 4 — Tempo adapter (DONE)

- Implemented `TempoClient` (`app/collectors/tempo_adapter.py`):
  `get_trace(trace_id)` wrapping `GET /api/traces/{traceID}`, and
  `search(params)` wrapping `GET /api/search` with a caller-supplied raw
  params dict (Tempo supports both legacy tag search and TraceQL — which
  one this cluster's version accepts is unverified, so the adapter
  doesn't assume a query shape, same "do not hardcode one query"
  discipline as Prometheus/Loki). Same never-raises `AdapterResult`
  pattern; a 404 (trace not found) is reported as `UNAVAILABLE` with a
  clear error rather than raising.
- Added `TempoClient.parse_spans()` and `parse_search_results()`:
  flatten raw Tempo responses into `Span` / `TraceSummary` records
  (trace ID, span ID, parent/child via `references[].refType==CHILD_OF`,
  service via `processes[processID].serviceName`, operation, duration,
  timestamps, error status — spec section 14's required fields).
  **Unverified assumption, same category as the Loki label-name issue**:
  `parse_spans()` assumes Tempo's `/api/traces/{id}` returns the
  Jaeger-compatible trace JSON shape (`data[].spans[]` /
  `data[].processes{}`), which is what Tempo's Query API is documented
  to implement for HTTP compatibility — but this has NOT been confirmed
  against this cluster's actual Tempo version. If
  `scripts/live_check_tempo.py` shows 0 spans parsed against a trace ID
  known to exist (e.g. one already visible in Grafana's Tempo explore
  view), the response is probably OTLP-shaped instead and `parse_spans`
  needs a second code path.
- 17 unit tests (`services/observation-gateway/tests/test_tempo_adapter.py`)
  covering the same failure modes as Prometheus/Loki (timeout, 5xx,
  connection error) plus Tempo-specific 404 handling, and `parse_spans`/
  `parse_search_results` edge cases (parent/child linkage, error-status
  detection via both `error` tag and `http.status_code`, malformed/empty
  data, spans missing required fields skipped without raising). Full
  suite: 51 tests passing (13 shared-model + 8 Prometheus + 13 Loki + 17
  Tempo), verified via
  `pytest shared/tests services/observation-gateway/tests -v`.
- Added `services/observation-gateway/scripts/live_check_tempo.py` — live
  smoke test: fetches a real trace by ID (or falls back to a search) and
  prints parsed `Span`/`TraceSummary` fields. Not yet run against your
  cluster.
- No new dependencies.

### Task 5 — Kubernetes adapter (DONE)

- Implemented `KubernetesClient` (`app/collectors/kubernetes_adapter.py`):
  read-only wrapper around the official `kubernetes` python client
  (added `kubernetes==35.0.0` to `requirements.txt` — the only new
  dependency across all four adapters so far). The client library is
  synchronous, so every call goes through
  `asyncio.wait_for(asyncio.to_thread(...), timeout=...)` to fit the same
  async interface as Prometheus/Loki/Tempo, and to get a uniform timeout
  even though the k8s client's own timeout handling differs per call.
  Same never-raises `AdapterResult` pattern (spec section 29):
  `ApiException` → `UNAVAILABLE` with the HTTP status, any other
  exception (connection refused, DNS failure, etc.) → `UNAVAILABLE`,
  `asyncio.TimeoutError` → `TIMEOUT`.
- Methods implemented (spec section 15, all read-only — no write
  operations exist in this class, deliberately): `list_pods`, `get_pod`,
  `list_deployments`, `get_deployment`, `list_replicasets`,
  `list_services`, `list_endpoints`, `list_events`, `get_nodes`,
  `get_namespaces`, `list_configmap_metadata`, `list_secret_metadata`.
- **Secret/ConfigMap safety (spec section 15 "Do NOT retrieve secret
  values")**: `list_secret_metadata`/`list_configmap_metadata` return
  `SecretMetadata`/`ConfigMapMetadata` Pydantic models that have no
  `data`/`string_data`/`binary_data` field at all — those attributes are
  never read off the underlying k8s objects anywhere in the mapping code,
  so a secret value cannot leak through this adapter's output regardless
  of how the result is later serialized or logged. Verified with a test
  that feeds a real-shaped secret payload in and asserts the returned
  object has no such attribute.
- **Event normalization (spec section 16)**: `list_events()` preserves
  the raw Kubernetes event `type` ("Normal"/"Warning") in `K8sEvent.severity`
  rather than mapping it to the canonical `Severity` enum
  (critical/warning/info/unknown) — that mapping is a normalization
  decision deferred to step 14, keeping with spec section 7 (the gateway
  does not reason, only collects/normalizes structurally). Also preserves
  `reason`/`message`/`resource` (`"<Kind>/<name>"`)/`namespace`/`timestamp`
  as required by spec section 16, so `CrashLoopBackOff`/`OOMKilled`/
  `FailedScheduling`/etc. all flow through as-is via the event's `reason`
  field rather than being pattern-matched here.
- Pod summaries also surface the *reason* behind a non-running container
  state (e.g. `CrashLoopBackOff` from `container.state.waiting.reason`),
  which is a second, independent path (besides the raw Event stream) to
  the same failure signals — useful since events eventually age out of
  the API server's retention window but pod status does not.
- 11 unit tests
  (`services/observation-gateway/tests/test_kubernetes_adapter.py`) built
  against real `kubernetes.client` model objects (`V1Pod`, `V1Deployment`,
  `CoreV1Event`, `V1Node`, `V1Secret`, etc.) with `unittest.mock.MagicMock`
  standing in for `CoreV1Api`/`AppsV1Api` — no live cluster or fake-config
  server needed. Covers: `ApiException` → `UNAVAILABLE`, connection error →
  `UNAVAILABLE`, artificial slow call → `TIMEOUT`, pod restart-count/
  readiness/CrashLoopBackOff-reason extraction, deployment replica-count
  and image extraction, event field preservation including the raw
  `severity` string, node readiness derived from `conditions`, and the
  two secret/configmap metadata-only safety tests described above. Full
  suite: 62 tests passing (13 shared-model + 8 Prometheus + 13 Loki + 17
  Tempo + 11 Kubernetes), verified via
  `pytest shared/tests services/observation-gateway/tests -v`.
- Added `services/observation-gateway/scripts/live_check_kubernetes.py` —
  live smoke test against the real k3s API server: lists
  `cloudmart-prod` pods/deployments/events and cluster nodes. Not yet run
  against your cluster. This one also implicitly re-verifies the
  ClusterIP DNS names in `settings.py`, since it needs to run from inside
  the cluster network namespace either way.
- **Design decision needing your confirmation**: `list_events()` uses the
  classic Core `v1.Event` API (`list_namespaced_event`), not the newer
  `events.k8s.io/v1 Event` API. Chose this because it's simpler (no
  extra `EventsV1Api` client needed) and is what kubelet still populates
  by default on k3s. Flag if you specifically need the newer API's
  richer fields (`series`, `action`, `reportingController`).

### Task 6 — Alertmanager webhook + normalization + FastAPI skeleton (DONE)

- **Live-cluster verification NOT done this task** — this session has no
  reachable k3s cluster (`kubectl config current-context` resolves to an
  EKS ARN that doesn't answer DNS from this sandbox, not the EC2/k3s box
  the spec describes). `live_check_*.py` scripts from Tasks 2-5 are still
  unrun. Flagging rather than fabricating: cannot confirm from here
  whether app-level metrics/traces are actually landing in
  Prometheus/Tempo, current node memory headroom, or Tempo's live restart
  status. Run all four `live_check_*.py` scripts from the EC2 box (or a
  session with real kubeconfig access) before trusting the ClusterIP DNS
  names/label conventions baked into `settings.py` and the adapters.
- Implemented `AlertmanagerAlert` / `AlertmanagerWebhookPayload`
  (`app/models/alertmanager.py`) matching Alertmanager's documented
  webhook receiver shape (version "4"). `alerts` must be non-empty and
  each alert's `status` must be `firing`/`resolved` — both enforced via
  Pydantic validators so FastAPI returns 422 automatically on a malformed
  body, no manual validation code needed in the route.
- Implemented `normalize_alert()` (`app/normalizers/alertmanager_normalizer.py`):
  one Alertmanager alert -> one canonical `Observation`. Same
  "don't assume one label convention" discipline as the Loki adapter —
  service/namespace/resource are resolved from a prioritized candidate
  list (`service`/`app`/`job`/`deployment`, etc.), not a single hardcoded
  label key, since PrometheusRule authors aren't guaranteed to use one
  convention. `commonLabels` (webhook-group-level) are merged under each
  alert's own labels so per-alert labels win on conflict, matching
  Alertmanager's own semantics. Severity maps `critical`/`warning`/`info`
  labels directly onto the canonical `Severity` enum; anything else
  (missing, unrecognized) -> `Severity.UNKNOWN` rather than guessing.
- Implemented `POST /webhooks/alertmanager` (`app/api/webhooks.py`):
  normalizes every alert in the payload, and for **firing** alerts only,
  creates one `Incident` per webhook delivery (title/severity/
  affected_services/initial_alerts derived from the batch, highest
  severity among the batch wins). **This is deliberately naive, not the
  full spec-section-7 dedup** — it only groups alerts arriving in the
  *same* webhook call; correlating alerts across *separate* deliveries by
  namespace/service/resource/time-window is step 9's job and needs a
  lookup against already-stored incidents, which doesn't exist yet.
  Resolved alerts are normalized and stored as Observations but do not
  open/close/touch an Incident, for the same reason (no lookup to know
  *which* incident they'd resolve). Firing observations get
  `correlation.incident_id` set to the new incident; resolved ones don't.
- Added `ObservationStore`/`IncidentStore` Protocols
  (`app/storage/interfaces.py`) plus `InMemoryObservationStore`/
  `InMemoryIncidentStore` (`app/storage/memory.py`) as the step-7 backing
  store. Chose this over deferring persistence entirely so the webhook
  handler is written once against the final interface shape — step 8
  swaps in Postgres-backed implementations of the same Protocols without
  touching `app/api/webhooks.py`.
- Stood up the FastAPI app (`app/main.py`, `create_app()` factory +
  module-level `app` for uvicorn): `GET /health`, `GET /ready`, and the
  webhook router. No auth yet (step 14, needs a real Kubernetes Secret to
  check against — nothing to check against until deployed). No
  `/incidents`, `/topology`, etc. yet — those need step 8-11 first.
- Added `cluster_name` to `Settings` (`cloudmart-k3s` default, env
  override) — needed by the normalizer for the Observation's required
  `cluster` field; no live API surfaces this name, so it's static config.
- 20 new tests: 12 in `test_alertmanager_normalizer.py` (severity mapping
  incl. unknown/missing, label-candidate fallback, commonLabels merge
  precedence, firing-uses-startsAt vs resolved-uses-endsAt, malformed
  timestamp doesn't raise, missing alertname, ID uniqueness), 8 in
  `test_alertmanager_webhook.py` via `fastapi.testclient.TestClient`
  (valid payload -> incident + observation, malformed/empty-alerts/bad-status
  -> 422, resolved-only -> no incident, multiple firing alerts in one
  delivery -> one incident, mixed firing+resolved -> only firing linked,
  incident retrievable from the store). Full suite: 82 tests passing,
  verified via `pytest shared/tests services/observation-gateway/tests -v`.
- Added `fastapi==0.115.0` / `uvicorn[standard]==0.30.6` to
  `services/observation-gateway/requirements.txt`; added `fastapi`/
  `httpx` to root `requirements-dev.txt` (TestClient needs both).
- **Design decision needing your confirmation**: incident IDs are
  `INC-<8 hex chars>` (e.g. `INC-A1B2C3D4`), not the sequential
  `INC-0001` shown in the spec's illustrative example — sequential
  numbering needs a single source of truth (a DB sequence), which
  doesn't exist until step 8's Postgres layer. Flag if you want this
  changed once Postgres is in place.

### Task 7 — TBD

Per the method sequence: step 15 (PostgreSQL persistence) is next —
implement Postgres-backed `ObservationStore`/`IncidentStore` against the
Protocols already defined in `app/storage/interfaces.py`, wire
`create_app()` to use them instead of the in-memory versions, and add the
`incidents`/`observations`/`evidence`/`deployments`/`service_topology`
tables (spec section 11). Redis buffering (also spec section 11) can
either land in this task or be split into its own — worth confirming
which you'd prefer before starting.
