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
- [x] 15. Implement PostgreSQL persistence — **DONE this task**
- [x] 16. Implement incident correlation — **DONE this task**
- [x] 17. Implement context builder — **DONE this task**
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

### Task 7 — PostgreSQL persistence (DONE)

- **Scoping call**: spec section 11 groups Postgres and Redis under one
  "Storage" requirement, but the numbered build order names this step
  "PostgreSQL persistence layer" specifically. Built Postgres only this
  task; Redis buffering is deferred until a concrete resilience scenario
  needs it (spec section 13's "don't lose observations when a backend is
  briefly unreachable" is about the *adapters*, not this store, so it
  doesn't obviously belong here either). Flag if you want it pulled
  forward.
- `app/storage/postgres/schema.sql`: all five spec-section-11 tables
  created as one idempotent migration (`CREATE TABLE IF NOT EXISTS`) —
  `incidents`, `observations`, `evidence`, `deployments`,
  `service_topology`. Only `incidents`/`observations` have a Python store
  implementation this task; the other three tables exist now purely so
  steps 10-12 don't need a schema change later, nothing writes to them
  yet. `observations.incident_id` is a nullable FK to `incidents` (`ON
  DELETE SET NULL`) — verified live that it actually rejects an
  observation pointing at a nonexistent incident
  (`test_observation_fk_rejects_unknown_incident_id`).
- `app/storage/postgres/pool.py`: `asyncpg` pool + a per-connection jsonb
  codec (`set_type_codec`) so `labels`/`metadata`/`affected_services`/etc.
  pass through as plain Python dict/list — store code never touches
  `json.dumps`/`loads` directly.
- `PostgresIncidentStore` / `PostgresObservationStore`
  (`app/storage/postgres/incident_store.py` /
  `observation_store.py`): implement the exact `IncidentStore`/
  `ObservationStore` Protocols from `app/storage/interfaces.py` — same
  interface the in-memory stores satisfy, so nothing above the store
  layer (the webhook handler) changed at all. `save()` is an upsert
  (`ON CONFLICT ... DO UPDATE`) on both.
- `app/main.py`: added a `lifespan` handler — if `settings.postgres_dsn`
  (env `POSTGRES_DSN`) is set, it opens the pool, applies the schema, and
  swaps `app.state.*_store` to the Postgres versions on startup; closes
  the pool on shutdown. Empty DSN (the default, and what every existing
  test uses) leaves the step-7 in-memory stores untouched — **zero
  changes needed to the 82 existing tests**, verified by rerunning them
  unmodified after this change.
- **Verified against a real, disposable local Postgres 14 instance**, not
  just mocks (asyncpg needs real SQL/type-codec behavior proven, a mock
  would just be asserting my own assumptions back at me):
  - `services/observation-gateway/tests/test_postgres_store_integration.py`
    — 6 tests (incident round-trip, get-missing returns None, save is a
    true upsert, observation round-trip preserves JSONB + correlation
    fields, FK-linked lookup by incident, FK rejects an unknown
    incident_id) run via `pytest.mark.anyio` (the `anyio` pytest plugin
    ships with `anyio` itself, already a transitive dependency via
    Starlette — no new test dependency added). Skipped by default
    (`pytestmark = pytest.mark.skipif(not POSTGRES_DSN, ...)`) so the
    normal `pytest` run stays fast and DB-free, same pattern as the
    `live_check_*.py` scripts for the other adapters. All 6 passed when
    actually run against `postgresql://incidentpilot@127.0.0.1:5433/incidentpilot`.
  - Beyond the test suite: started the real `uvicorn` server with
    `POSTGRES_DSN` pointed at that same instance, POSTed a real
    Alertmanager webhook payload to it, then independently queried the
    tables with raw `psql` (not through the app) and confirmed the
    incident and observation rows, JSONB `labels`, and the FK link all
    landed correctly — then killed the server process and re-queried to
    confirm the data survives (unlike the in-memory store, which loses
    everything on restart — this was the actual point of this task).
  - The scratch Postgres instance used for this was ephemeral (a local
    `initdb` under the session's scratchpad dir, port 5433) and has been
    stopped; it was not the CloudMart cluster's Postgres, since no live
    cluster is reachable from this sandbox (still unresolved from Task
    6 — see that entry).
- `asyncpg==0.31.0` added to `services/observation-gateway/requirements.txt`
  and root `requirements-dev.txt`.
- Full suite: **82 passed, 6 skipped** (the 6 skips are the Postgres
  integration tests, skipped because this environment has no
  `POSTGRES_DSN` configured by default) — `pytest shared/tests
  services/observation-gateway/tests -v`.

### Task 8 — Incident correlation/deduplication (DONE)

- Replaced the naive "one incident per webhook delivery" behavior from
  Task 6 with the real spec-section-7 rule, in a new
  `app/correlation/incident_correlator.py`
  (`correlate_or_create_incident()`): a batch of firing observations
  merges into an existing **OPEN** incident if that incident's
  `affected_namespace` matches, at least one `affected_services` entry
  overlaps, and it was `updated_at` within `settings.correlation_window_minutes`
  (default 15, env `CORRELATION_WINDOW_MINUTES`) — otherwise a new
  incident is created, same as before. No AI/ML, no fuzzy matching, per
  the spec's explicit constraint. On merge: `affected_services` and
  `initial_alerts` are unioned (no duplicates), `severity` becomes the
  higher of the two, `updated_at` bumps to now — `title`/`created_at`/
  `incident_id` are untouched (the incident's identity doesn't change).
  **Deliberately does not touch `status`/resolution** — a resolved alert
  still doesn't close or affect an incident at all; that's lifecycle
  management, a different concern from correlation, and stays out of
  scope here.
- Added `IncidentStore.find_correlation_candidates(namespace, services,
  since)` to the Protocol (`app/storage/interfaces.py`) plus both
  implementations: `InMemoryIncidentStore` filters in Python;
  `PostgresIncidentStore` does it in SQL (`status = 'open' AND
  affected_namespace IS NOT DISTINCT FROM $1 AND updated_at >= $2 AND
  affected_services ?| $3::text[]`) — the `?|` jsonb operator checks
  array-element overlap directly, no application-side filtering needed.
  Both return `[]` immediately if `services` is empty, since namespace
  alone was judged too broad a match to merge safely (e.g. two
  completely unrelated alerts that both happen to lack a service label
  would otherwise collide into one incident).
- Tie-break when more than one OPEN incident matches: the most recently
  `updated_at` one wins (`test_multiple_candidates_tie_break_to_most_recently_updated`).
- `app/api/webhooks.py` simplified: `_build_incident`/`_highest_severity`
  moved into the correlator module (same logic, now reused for both the
  "new incident" and "merge" paths); the handler just calls
  `correlate_or_create_incident()` once per delivery.
- 12 new unit tests (`test_incident_correlator.py`, in-memory store, no DB
  needed): first delivery creates, second delivery same
  namespace/service merges, exact duplicate refiring doesn't duplicate
  `initial_alerts`, different namespace/service each create separately,
  no-derivable-service never merges, outside the time window creates
  separately, a resolved incident is never a merge candidate, tie-break
  logic. Plus 3 new webhook-level tests
  (`test_alertmanager_webhook.py`) proving this works through the actual
  HTTP endpoint across *separate* POST requests, not just within one
  payload. Plus 5 new Postgres integration tests for
  `find_correlation_candidates` itself (namespace/service/window
  matching, excludes resolved, excludes stale, requires service overlap,
  empty services short-circuits) — all run and passed against the same
  real local Postgres instance from Task 7. Full suite: **94 passed, 11
  skipped** (11 = all Postgres integration tests, skipped without
  `POSTGRES_DSN`) in-memory; **11 passed** when the Postgres-only file is
  run with `POSTGRES_DSN` set.
- **Verified live, end to end, reproducing the exact gap flagged at the
  end of Task 7**: started the real server against a real (truncated)
  Postgres, POSTed the same alert twice as two separate HTTP requests
  (previously: 2 incidents — now: 1, confirmed via raw `psql`, not just
  the app's own response), then a related alert on the same service as a
  third separate request (merged into the same incident,
  `initial_alerts` grew to both names), then an alert on a *different*
  service as a fourth request (correctly created a second, separate
  incident). Killed the server and stopped the scratch Postgres
  afterward — nothing left running.

### Task 9 — Incident Context Builder (DONE)

- New `app/context/incident_context_builder.py`
  (`IncidentContextBuilder.build(incident)`), triggered from
  `app/api/webhooks.py` via FastAPI `BackgroundTasks` after a firing
  webhook creates/merges an incident (spec section 5's "kicks off context
  collection") — the webhook still returns 202 immediately (~50ms,
  verified live below); collection happens after the response is sent so
  a slow/unreachable backend can never turn into a webhook timeout.
- Pulls a `settings.context_window_minutes`-wide window (default 15,
  spec section 9's suggestion) of: the incident's already-linked alert
  Observations (cited as `EvidenceType.ALERT`), Prometheus metrics, Loki
  error logs, Tempo error spans, and Kubernetes events + pod status. Sets
  `current_phase = COLLECTING_CONTEXT` at the start and
  `READY_FOR_INVESTIGATION` at the end — **always** the end state, even
  if every single source failed (verified both by test and live run
  below), since "no data collected" still means the incident is ready to
  be looked at.
- **Query selection decided now** (deferred since Task 2):
  `_METRIC_PROBES` in the context builder — 4 PromQL templates
  (`kube_pod_container_status_restarts_total`,
  `container_cpu_usage_seconds_total`,
  `container_memory_working_set_bytes`,
  `traefik_service_requests_total`), a namespace-scoped Loki error-keyword
  query, and a Tempo tag search per affected service (capped to 5 traces
  fetched per service). **None of these are confirmed against the real
  cluster** — see `docs/LIVE_CLUSTER_VERIFICATION.md` (new this task),
  which is the instruction set for closing that out from the EC2 box.
  Nothing else depends on the exact query strings, so wrong ones are a
  one-line fix once verified.
- 4 new normalizers (`app/normalizers/{prometheus,loki,tempo,kubernetes}_normalizer.py`)
  turn each adapter's already-parsed shape (`LogEntry`/`Span`/`K8sEvent`/
  `PodSummary`, all built in Tasks 2-5) into canonical Observations. This
  is also where Task 5's deferred K8s event severity mapping
  (`Normal`->INFO, `Warning`->WARNING) finally happens. Deliberately
  conservative: no severity inference from free-text log messages, no
  latency-threshold judgment on spans — only structural mapping of
  fields the source already provided. Logs capped at 50 entries/service
  (spec section 5's "don't dump unbounded raw log volume").
- Every Observation collected gets exactly one `Evidence` record citing
  it (`raw_reference.query` = the real query/reference used) — this is
  what closes spec section 8's provenance requirement for real telemetry,
  not just the Alertmanager-sourced observations from Task 6.
- New `EvidenceStore` Protocol (`app/storage/interfaces.py`) +
  `InMemoryEvidenceStore` + `PostgresEvidenceStore` — same
  Protocol-then-swap pattern as Task 7/8's Observation/Incident stores.
  `app/main.py` now also constructs the four adapter clients at startup
  (Kubernetes client construction is wrapped in try/except: no reachable
  kubeconfig -> `None`, and the Context Builder treats a `None` client as
  source `UNAVAILABLE` rather than crashing).
- **Resilience (spec section 13) is the actual point of this task, and is
  tested three ways, not just asserted:**
  - 9 unit tests (`test_incident_context_builder.py`, mocked adapters):
    one source unavailable doesn't block the others, a timeout is
    reported not raised, a missing Kubernetes client doesn't crash the
    app, all four sources failing still reaches
    `READY_FOR_INVESTIGATION`, every Evidence cites a real Observation,
    and running `build()` twice doesn't duplicate the alert Evidence
    (idempotency, since correlation (Task 8) can call this multiple
    times as an incident gets merged into across deliveries).
  - 20 more unit tests across the four normalizers
    (`test_{prometheus,loki,tempo,kubernetes}_normalizer.py`).
  - **Verified live against genuinely unreachable hosts** — not mocks:
    started the real server with `POSTGRES_DSN` pointed at a real local
    Postgres but left `PROMETHEUS_BASE_URL`/`LOKI_BASE_URL`/
    `TEMPO_BASE_URL` at their real (in-cluster-only, therefore actually
    unreachable from this sandbox) defaults, and the Kubernetes client
    pointed at the same unreachable EKS ARN from Task 6. POSTed a real
    webhook — response returned in the ~50ms range as expected. Polled
    the incident's `current_phase` in Postgres every 5s: it sat in
    `collecting_context` for ~25s (all four backends genuinely timing
    out/failing to resolve, one after another) then correctly reached
    `ready_for_investigation`. Confirmed via `psql` that the alert
    Evidence (`ev-...`, type=alert, source=alertmanager,
    "Alert 'HighHTTPErrorRate' fired on order-service") was persisted
    despite all telemetry sources failing, and the uvicorn log showed no
    unhandled exception. This is the exact "Tempo mid-restart-loop, still
    get partial context" scenario from the original spec's Step 0,
    proven with a real (if accidental) unreachable-backend condition
    rather than a mock standing in for one.
  - A gotcha found and fixed along the way: the webhook test fixture
    originally used `create_app()` unmodified, which wires the *real*
    adapter clients at their real cluster-only URLs — since
    `TestClient` runs `BackgroundTasks` synchronously before returning,
    every firing-alert webhook test started making real network calls
    against `*.svc.cluster.local` hostnames, which resolve very slowly
    on this sandbox (looked like a hang, not a fast NXDOMAIN — a full
    `pytest` run exceeded 120s and had to be killed). Fixed by blanking
    out all four `app.state.*_client` attributes to `None` in the
    webhook test fixture — the Context Builder's already-tested
    None-client path reports each source `UNAVAILABLE` immediately, no
    network I/O at all. Worth knowing if you add more webhook tests:
    the fixture already handles this, no action needed, but don't
    remove those four `None` assignments.
  - 2 more Postgres integration tests
    (`test_postgres_store_integration.py`): `PostgresEvidenceStore`
    round-trip (including `RawReference` round-tripping through JSONB)
    and upsert. Full Postgres-only suite: 13 passed.
- One existing test's assertion was stale, not wrong: `test_incident_visible_via_incident_store`
  expected `current_phase == "detected"`, which was correct before this
  task (nothing advanced the phase past DETECTED) but is now genuinely
  wrong — the Context Builder runs synchronously inside that same test
  (via `TestClient`'s background-task execution) and correctly advances
  it to `ready_for_investigation`. Updated the assertion; this was a
  fixture catching up to new correct behavior, not a bug.
- Full in-memory suite: **123 passed, 13 skipped** (13 = all Postgres
  integration tests, skipped without `POSTGRES_DSN`) in 8s —
  `pytest shared/tests services/observation-gateway/tests -v`.
- **Deliberately NOT built this task** (spec section 9 lists them, but
  the numbered build order splits them out): deployment status/info
  (step 19/12 — needs the deployment-context collector, which doesn't
  exist) and service topology (step 18/11 — needs the dependency graph,
  which doesn't exist). The Context Builder does not attempt either.

### Task 10 — TBD

Per the method sequence, step 18 (service topology) is next. Still
outstanding, now written up as an actual runnable procedure rather than
just a note: `docs/LIVE_CLUSTER_VERIFICATION.md` (new this task) is the
step-by-step for verifying all four adapters — and, critically, the
`_METRIC_PROBES`/log query/trace search added in this task — against the
real CloudMart cluster from the EC2 box. Two things flagged as
highest-value to check first: whether application-level traces exist in
Tempo *at all* (a separate inspection found no OpenTelemetry SDK in the
CloudMart app code, which would mean Tempo has nothing to find regardless
of adapter correctness), and Tempo's live restart status
(`kubectl describe pod tempo-0 -n observability`), since the Context
Builder's resilience path was specifically built to tolerate that.
