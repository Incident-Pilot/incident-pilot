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
- [x] 18. Implement topology — **DONE this task**
- [x] 19. Implement deployment context — **DONE this task**
- [x] 20. Implement security context — **DONE this task**
- [x] 21. Implement API — **DONE this task**
- [x] 22. Deploy to k3s — **manifests + deploy automation DONE this task; not yet actually applied, see below**
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

### Task 10 — Service topology (DONE)

- New `app/topology/service_topology_builder.py`
  (`ServiceTopologyBuilder.build(namespace)`), exposed via new
  `GET /topology` (`app/api/topology.py`). Merges three sources into one
  adjacency-list graph (spec section 10):
  1. **Static seed** — the exact call chain the spec documents from the
     CloudMart app's own code (`frontend -> product-service/order-service/
     user-service`, `order-service -> product-service/notification-service`).
     Not an inference; this is what the code does. Matches spec section
     15's illustrative `"topology"` example edge-for-edge, which is a
     good sign the seed is right.
  2. **Kubernetes Services** (`KubernetesClient.list_services`) — adds
     every Service in the namespace as a node, even ones with no known or
     observed edges yet, so the graph doesn't silently omit a service
     just because nothing calls it (yet).
  3. **Tempo-observed spans** — for each known service, searches recent
     traces (capped at 5 per service, same pattern as the Context
     Builder's trace collection) and walks parent/child span pairs: a
     child span whose `service` differs from its parent's `service` is a
     real observed call, added as an edge if not already present. Purely
     structural (reads `Span.service`/`parent_span_id` directly), no
     inference about which edges "matter."
  - All three sources merge by union — nothing is scored, weighted, or
    filtered. Kubernetes or Tempo being unreachable degrades to "topology
    built from fewer sources" (verified both by test and live run below),
    never an error; the static seed alone is always enough to answer the
    endpoint.
  - New `TopologyStore` Protocol (`app/storage/interfaces.py`) +
    `InMemoryTopologyStore` + `PostgresTopologyStore` — same
    Protocol-then-swap pattern as every other store so far, backed by the
    `service_topology` table that's existed since Task 7's migration but
    had no store implementation until now. `save_service()` is an upsert
    keyed on `service` (matches the table's primary key).
  - `GET /topology` recomputes live on every call (no caching/scheduling
    layer — topology isn't hit per-incident the way context collection
    is, so this was judged simple enough not to need one) and persists
    the result before returning it, so the stored table always reflects
    the most recent call.
  - Added `settings.default_namespace` (`cloudmart-prod`) — topology
    isn't incident-scoped, so it needs its own namespace source instead
    of reading `incident.affected_namespace`.
- 7 unit tests (`test_service_topology_builder.py`, mocked K8s/Tempo):
  static seed alone with no live sources, every service persisted,
  K8s services added as nodes, K8s-unavailable doesn't block the static
  seed, a real Tempo-observed span adds a new edge without duplicating an
  edge the static seed already had, Tempo-unavailable doesn't block K8s
  or the static seed, and no sources at all still returns the seed
  without crashing. Plus 2 endpoint tests
  (`test_topology_api.py`) and 2 new Postgres integration tests
  (round-trip, upsert) — full Postgres-only suite now 15 passed.
- **Verified live against the same genuinely-unreachable cluster hosts as
  Task 9**: started the real server against real local Postgres, `GET
  /topology` took ~25s (real K8s + 5× real Tempo search attempts each
  timing out) then correctly fell back to the static seed alone — no
  crash, 200 OK. Confirmed via raw `psql` that all 5 services (including
  the two leaf services with empty `depends_on`) were persisted to
  `service_topology` correctly. Server log showed no unhandled exception.
  Stopped both afterward.
- Full in-memory suite: **132 passed, 15 skipped** in 8.6s.

### Task 11 — Deployment context (DONE)

This task touches **two repositories** — noting that explicitly since
every prior task only touched this one.

- **`ecommerce-cloudmart` repo** (`~/Ascentic/ecommerce-cloudmart-main`
  locally — not a git repo in this sandbox, so no commit made there, just
  the file edit): `deploy.sh` now captures `COMMIT_SHA`/`BRANCH`/
  `DEPLOYED_AT` right after `git pull` and, after the existing `kubectl
  apply` block (before the rollout restart), loops over the same five
  services annotating each Deployment: `incidentpilot.io/commit-sha`,
  `incidentpilot.io/branch`, `incidentpilot.io/deployed-at`. ~10 lines
  added, nothing else changed — no new files, no image-tagging change, no
  pipeline redesign, exactly what spec section 5 asked for ("the smallest
  possible metadata emission"). Verified two ways since there's no live
  cluster to actually run this against: `bash -n deploy.sh` (syntax
  check) and a dry run with a fake `kubectl` on `$PATH` that just echoes
  its args — confirmed the exact five `kubectl annotate` commands
  construct correctly, with annotation keys matching character-for-character
  what the gateway-side collector reads (see below).
- **This repo (`incidentpilot`)**:
  - `DeploymentSummary` (`app/collectors/kubernetes_adapter.py`) gained
    an `annotations: Dict[str, str]` field, populated from
    `dep.metadata.annotations` in `_to_deployment_summary` — the only
    change to an existing adapter this task made. Additive/optional, so
    no existing test broke (2 new tests added: annotations preserved,
    defaults to `{}` when the Deployment has none — e.g. one that
    predates this task's `deploy.sh` change).
  - New canonical `Deployment` model (`shared/models/deployment.py`) —
    `commit_sha`/`branch`/`image_tag`/`rollout_revision`/`deployed_at`/
    `success`, matching spec section 5's field list exactly. Added
    `ALTER TABLE deployments ADD COLUMN IF NOT EXISTS branch TEXT` to
    `schema.sql` — Task 7's original migration missed `branch`; idempotent
    `ADD COLUMN IF NOT EXISTS` so it's safe against a database that
    already has the table (verified live, see below).
  - `DeploymentContextCollector` (`app/deployment/deployment_context_collector.py`):
    calls `KubernetesClient.get_deployment()`, parses the three
    `incidentpilot.io/*` annotations plus Kubernetes' **own**
    `deployment.kubernetes.io/revision` annotation (no app-side change
    needed for that one — k8s sets it automatically on every Deployment).
    `success` is deliberately **not** read from an annotation — it's
    derived from the Deployment's live `ready_replicas`/`replicas`/
    `unavailable_replicas` at collection time, since a deploy's real
    outcome isn't known until the rollout actually finishes, and
    re-checking current status is strictly more accurate than trusting a
    flag stamped at `kubectl apply` time.
  - New `normalize_deployment()` (`app/normalizers/deployment_normalizer.py`)
    turns a `Deployment` into an Observation (`signal_type=DEPLOYMENT_EVENT`,
    `source=GIT` — matching the source already used in Task 1's own
    Evidence test fixture, taken as a design-intent signal rather than
    picked arbitrarily). This keeps deployment evidence on the *same*
    Observation-then-Evidence provenance chain as every other context
    source, rather than being a special case with no observation to cite.
  - `DeploymentStore` Protocol + `InMemoryDeploymentStore` +
    `PostgresDeploymentStore` — same pattern as every store so far.
    `get_latest(service)` is the one query the Context Builder needs.
  - **Wired into the Context Builder** (`_collect_deployment_context`,
    called from `build()` after Kubernetes events/pods): for each
    affected service, collects the current Deployment, and if found,
    creates Evidence with a summary text matching spec section 15's
    illustrative example exactly — `"order-service deployed 4 minutes
    before this incident (commit abc1234)"` — a plain time delta between
    `incident.created_at` and `deployment.deployed_at`, not a causal
    claim. `deployment` is now a 5th independently-tracked source
    alongside prometheus/loki/tempo/kubernetes in
    `IncidentContextResult.source_statuses` — same resilience contract:
    unreachable K8s reports `deployment: UNAVAILABLE` and every other
    source still completes.
  - 7 new tests for the collector (annotation parsing, persistence,
    missing-`deployed-at` fallback to `created_at`, unreachable ->
    `(None, status)` not a crash, no-k8s-client -> `UNAVAILABLE`,
    `success` derivation both ways), 3 for the normalizer, 4 new
    Context-Builder-level tests (time-delta evidence text end-to-end,
    evidence cites a real observation, persists to the store, no
    deployment found -> no evidence but still reaches
    `READY_FOR_INVESTIGATION`), 4 new shared-model tests for `Deployment`
    itself, 4 new Postgres integration tests (round-trip including the
    new `branch` column, `get_latest` picks the most recent, missing
    service -> `None`, upsert). Full suite: **152 passed, 19 skipped**
    in-memory; all 19 Postgres-only tests run and passed against the
    same real local Postgres instance used since Task 7.
  - **Verified live** (same real-unreachable-cluster rigor as every prior
    task): real server, real Postgres, POSTed a webhook — response fast
    as before, background context collection took ~25s working through
    all five now-tracked sources (the deployment collector's one extra
    real `get_deployment` call added no meaningfully additional delay),
    landed on `READY_FOR_INVESTIGATION`, zero unhandled exceptions in the
    server log. Confirmed via `psql` that `deployments` stayed empty
    (correct — no reachable cluster to read a real Deployment from) while
    the alert Evidence still persisted correctly, exactly the same
    graceful-partial-context behavior proven in Tasks 9-10, now extended
    to a 5th source.

### Task 12 — Security findings ingestion (DONE)

Also touches both repos, same as Task 11.

- **`ecommerce-cloudmart` repo**: `deploy.sh` now POSTs each report to the
  gateway right after generating it — `reports/gitleaks-report.json` to
  `POST /ingest/gitleaks`, each `reports/trivy-${svc}.json` to
  `POST /ingest/trivy?service=${svc}` — via a new `INCIDENT_GATEWAY_URL`
  env var. Deliberately **empty by default**: the gateway isn't deployed
  into the cluster yet (that's step 21/22, still ahead), so there's no
  real URL to point at yet — same bootstrapping order problem as the
  Alertmanager webhook's URL (also still unwired, for the same reason).
  Every curl is guarded (`[ -n "$INCIDENT_GATEWAY_URL" ] && [ -s
  reports/... ]`) and best-effort (`|| true`), so this never fails a
  deploy — running with the var unset is a no-op, exactly today's
  behavior. Verified the exact commands construct correctly via a dry
  run with a fake `curl`/report files on `$PATH`, **then actually ran
  the real `curl` commands** (not a fake) against the real running
  gateway + real Postgres — see below.
- **This repo**:
  - `app/models/security_reports.py`: `GitleaksFinding` uses
    `extra="ignore"` — the *only* payload model in this service that
    does, deliberately. Real Gitleaks JSON includes the actual leaked
    secret value in `Secret`/`Match` fields; `extra="ignore"` drops them
    at parse time, before they become a Python attribute on anything —
    stronger than "the normalizer just doesn't read that field," since
    there's nothing for any future code to accidentally read. Verified
    directly (`not hasattr(finding, "Secret")`), not just inferred.
  - `normalize_gitleaks_findings()` / `normalize_trivy_report()`
    (`app/normalizers/`): Gitleaks findings always map to
    `Severity.CRITICAL` (an actual credential in git history is
    unambiguously critical, not a judgment call) and derive `service`
    from the `services/<name>/...` file path convention. Trivy severity
    is a direct passthrough map (CRITICAL->CRITICAL, HIGH/MEDIUM->WARNING,
    LOW->INFO, UNKNOWN/anything else->UNKNOWN) — same "map the tool's
    own label, don't add judgment" discipline as every other normalizer
    — and derives `service` from `ArtifactName`
    (`localhost:5000/cloudmart/order-service:v1` -> `order-service`,
    handling the registry-host-also-has-a-colon gotcha), overridable via
    an explicit `service` query param. Trivy findings capped at 200,
    highest-severity-first, since a real image scan can return hundreds
    of vulnerabilities — the same "don't dump unbounded volume" guard
    used for Loki log lines.
  - `POST /ingest/gitleaks` / `POST /ingest/trivy`
    (`app/api/security_ingestion.py`): both 202 + observation IDs on
    success, 422 on a malformed body (wrong top-level JSON type), 202
    with zero observations on an empty/clean report (no findings isn't
    an error condition). Ingestion only — no blocking of the deploy that
    triggered the scan, no opinion on `deploy.sh`'s existing
    non-blocking `--exit-code 0` choice.
  - 14 normalizer unit tests (severity mapping both directions, service
    derivation including the registry-colon edge case, capping keeps the
    most severe, malformed dates, missing fields) + 9 endpoint tests
    (valid/empty/malformed for both, service-override precedence) — one
    normalizer test and one endpoint test specifically assert the dummy
    secret value used in the fixture never appears anywhere in the
    Observation's serialized output, not just "the field isn't named
    Secret." Full suite: **175 passed, 19 skipped** (Postgres-only,
    unaffected by this task — no new Postgres store was needed since
    security findings reuse `ObservationStore`, already built).
  - **Verified live, with real (not simulated) `curl` calls**: real
    server, real Postgres, ran the *actual* `deploy.sh`-shape commands
    (`curl -sf -X POST .../ingest/gitleaks --data @reports/gitleaks-report.json`,
    same for trivy) against dummy report files containing a fake secret
    value (`AKIA_TEST_DUMMY_NOT_REAL_00000000`, never a real credential).
    Both returned 202 with the right counts. Then, independent of the
    app, ran a raw SQL query across every text/JSONB column in
    `observations` (`metadata::text LIKE '%AKIA_TEST_DUMMY%'`, same for
    `labels`/`signal`/`resource`) — **zero rows**, confirming the secret
    genuinely never reached the database, not just that the app's own
    response didn't echo it back. Severity mapping, service derivation,
    and commit/author metadata all landed correctly. No exceptions in
    the server log. Stopped both afterward.

**Note found while running the full suite after this task, unrelated to
anything built here**: `app/collectors/tempo_adapter.py` has been
substantially rewritten since Task 4/9/10 — its own docstring now says
the response shape is "CONFIRMED against the live cluster (Tempo 2.9.0)"
as OTLP-JSON (`batches`/`scopeSpans`), not the originally-assumed
Jaeger shape, with base64 trace/span IDs and OTLP typed-union attributes
now handled, and the old Jaeger parser kept as a defensive fallback. This
is real, valuable live-verification work — presumably yours, done in
parallel via `docs/LIVE_CLUSTER_VERIFICATION.md` while this task was in
progress — and I did not touch that file. One pre-existing test,
`test_parse_spans_treats_error_tag_true_as_error_even_without_status_code`,
now fails: the new shared `_is_error_span()` helper (used by both the
OTLP and Jaeger code paths) checks `status.code` and
`tags["http.status_code"]` but no longer checks a boolean `tags["error"]`
attribute, which the original Jaeger-only implementation did and which
that test still expects. Every other test in the suite passes (174
passed, 19 skipped, 1 deselected when this one test is excluded) — this
looks like a real, narrow regression from unifying the two code paths'
error-detection logic, not something in scope for this task to fix,
since it's inside actively-in-progress work on a file I don't own the
current edits to.

### Task 13 — Live verification results (Prometheus/Loki/Tempo/K8s adapters, real cluster) + fixes (DONE)

The `docs/LIVE_CLUSTER_VERIFICATION.md` procedure was run for real, in
parallel with Task 12, against the actual CloudMart EC2/k3s cluster — the
first genuine live-cluster contact this project has had (every prior task
was mocked or ran against a scratch local Postgres, explicitly flagged as
such throughout Tasks 6-12). This session shares the same git checkout as
that verification work — `git log` shows two of its commits already on
`main` (`55483de` live_check_loki.py update, `c33370e` Tempo OTLP fix) —
so results and code changes from that session are directly visible here,
not just reported secondhand.

**Confirmed, no code changes needed:**
- **Kubernetes adapter** — `list_pods`/`list_deployments`/`list_events`/
  `get_nodes` all `available` with real `cloudmart-prod` data. Notably,
  `list_events()` is what surfaced the real root cause of the node's
  ongoing pod-eviction problem (see below) — a `Warning: Evicted` event
  with an explicit `ephemeral-storage` reason, carried through untouched
  by the adapter's existing raw `reason`/`message` passthrough design, no
  special-casing needed. Added a confirmation note to the adapter's
  module docstring.
- **Loki adapter** — real Promtail labels matched the candidate-list
  resolution exactly (`namespace`/`pod`/`container` direct,
  `service` via `app`). One gap found: a `service_name` label also exists
  on this cluster but wasn't in `_SERVICE_LABEL_CANDIDATES` — currently
  harmless (`app` already matches first and always agrees with it here),
  but added as a second candidate for future robustness, plus a new test
  (`test_parse_entries_service_falls_back_to_service_name_label`).
- **Auth assumption** (`app/config/settings.py`) — every live call above
  came back `available`, never 401/403, across Prometheus/Loki/Tempo/K8s.
  The "treat these as unauthenticated" assumption is now confirmed
  correct in practice, not just untested; docstring updated to say so.

**Partially confirmed:**
- **Prometheus adapter / `_METRIC_PROBES`** — `pod_restarts`
  (`kube_pod_container_status_restarts_total`) confirmed returning real
  per-pod data. `http_error_rate`'s metric name
  (`traefik_service_requests_total`) confirmed to exist on this
  Prometheus, but not yet confirmed to return non-empty data for
  cloudmart-prod traffic specifically. `cpu_usage_seconds` and
  `memory_working_set_bytes` — still completely unverified, exactly as
  originally flagged. Updated the `_METRIC_PROBES` comment in
  `incident_context_builder.py` to state per-probe status precisely
  rather than one blanket "unverified" note covering all four unevenly.
  (Separately confirmed, and worth recording since it could otherwise
  look like a bug later: a plain `up{namespace="cloudmart-prod"}` query
  returns empty — expected, not a probe, since there's no ServiceMonitor
  scraping the app pods directly.)

**Found and already fixed by the parallel session — I verified, did not
redo:**
- **Tempo adapter** — real shape is OTLP-JSON (`batches`/`scopeSpans`,
  base64 trace/span IDs, typed-union attributes), not the originally
  assumed Jaeger shape. Fixed in `c33370e`, already on `main`. Also
  confirmed: the app **does** have real OpenTelemetry instrumentation at
  runtime (a genuine `order-service` Express-middleware span was
  captured) — this overturns the earlier static-code-inspection finding
  from the original scenario brief ("no OpenTelemetry SDK anywhere"). Most
  captured traces this session were kube-probe health checks rather than
  real order-flow traffic, so an end-to-end test should generate actual
  app traffic (place a real order) before trusting trace evidence.

**Found by me, fixed this task** — a narrow regression introduced when
`c33370e` unified the OTLP and Jaeger error-detection paths onto one
shared `_is_error_span()` helper: it checks `status.code` and
`http.status_code` but had dropped the boolean `tags["error"]` check the
original Jaeger-only path had, breaking
`test_parse_spans_treats_error_tag_true_as_error_even_without_status_code`.
Restored the check as an additional `or` condition (safe for both paths —
checking an extra boolean only adds detection coverage, never removes
any). All 17 Tempo adapter tests pass again; full suite back to 100%
(176 passed, 19 skipped — one more than Task 12's 175 thanks to the new
Loki test above).

**Real infrastructure finding, explicitly not this service's job to fix
(same "infra concern for the user to address separately" boundary the
original spec brief drew around Tempo's stability) — recorded here so the
context isn't lost, no code changed in response:**
- **Node ephemeral storage, not memory, is the actual eviction trigger** —
  confirmed via a real `Evicted` K8s event: ~870MB free against a ~1.02GB
  threshold. Likely self-reinforcing: hundreds of uncollected
  `Evicted`/`Completed`/`Error` pods (some 6+ days old, including ~180
  from the permanently-broken `kube-events-kubernetes-event-exporter`
  Deployment) are holding onto logs/writable-layer disk space, which
  triggers more evictions, which produces more uncollected pods. This is
  real-world validation that the resilience patterns built across Tasks
  9-12 (every adapter degrades to `unavailable`/`timeout` rather than
  failing the whole request) are load-bearing, not defensive-programming
  theater — remediation (`kubectl delete pods --field-selector=status.phase=Failed`
  etc.) is a cluster-ops action for you to take, not something this
  service should ever do automatically (this service does not do
  Kubernetes writes at all, by design — spec section 3/16).

**Explicitly not evaluated or acted on by me** (yours to resolve, not
blocking any further build-order step): the `master`/`origin/main`
branch-divergence git housekeeping on the EC2 clone (this sandbox's
checkout shows a clean, non-divergent `main` — whatever divergence
existed was on that other clone, already resolved there per your report),
and confirming Alertmanager's `alertmanager.yaml` actually routes to the
gateway's webhook (that's deployment-time wiring, step 22 territory, same
as the gateway's own in-cluster URL not existing yet for
`INCIDENT_GATEWAY_URL`/security ingestion in Task 12).

**A note on working concurrently on the same checkout**: this session
never runs `git commit` unless asked, so all Task 6-13 work (~25 files)
exists only as uncommitted changes/untracked files in the working tree —
safely layered on top of whatever the parallel session has committed,
since committing elsewhere doesn't touch uncommitted local changes. Worth
being aware of if a destructive git command (`git checkout .`,
`git reset --hard`) ever runs from either session — it would discard the
other's in-progress work.

Full suite: **176 passed, 19 skipped** —
`pytest shared/tests services/observation-gateway/tests -v`.

### Task 14 — API layer + authentication (DONE)

Completes the spec section 12 endpoint list — everything except
`/health`/`/ready`/`/webhooks/alertmanager` (steps 7, mostly),
`/topology` (step 11) was still missing until this task.

- **Auth** (`app/api/auth.py`): a single shared `require_api_key`
  dependency, applied per-router via
  `APIRouter(dependencies=[Depends(require_api_key)])` — covers
  webhooks, ingestion, topology/services, and the new incidents routes.
  `/health`/`/ready` stay exempt (defined directly on `app`, outside any
  authenticated router) — k8s liveness/readiness probes hit these
  unauthenticated by convention, and they reveal nothing sensitive.
  **Fails closed**: an unset `GATEWAY_API_KEY` rejects every protected
  request with 503, not "no auth" — a missing Kubernetes Secret must
  never silently become an open gateway. Token comparison uses
  `hmac.compare_digest` (constant-time), not `==` — avoids a timing
  side-channel on the secret comparison, unprompted but worth doing by
  default for anything checking a real credential.
- **`GET /incidents`** (`app/api/incidents.py`): all incidents, newest
  first, optional `?status=` filter.
- **`GET /incidents/{id}`**: composite response matching spec section
  15's illustrative shape almost exactly — the incident's own fields,
  plus `observations` (ID list), `evidence` (summarized:
  `id`/`type`/`summary`), and `topology` (a subgraph limited to
  `affected_services`, read from the `TopologyStore` — a fast local
  read, not a fresh live K8s/Tempo rebuild per incident). 404 if the
  incident doesn't exist.
- **`GET /incidents/{id}/observations`** / **`/evidence`**: full
  `Observation`/`Evidence` objects (the composite view above only gives
  IDs/summaries) — same 404-if-missing-incident behavior, checked
  explicitly rather than just returning an ambiguous empty list.
- **`GET /incidents/{id}/timeline`**: observations + evidence merged
  into one chronologically-sorted list, each entry tagged `kind`
  (`observation`/`evidence`). Not spec'd in detail, so this shape is a
  reasonable interpretation, not a fixed contract — the "what happened
  when" view a future RCA agent or a human would want.
- **`GET /services`** (added to `app/api/topology.py`, since it's the
  same data source): flat sorted list of service names from the
  `TopologyStore` — deliberately minimal (spec section 12 lists the
  endpoint without further detail); richer per-service status is already
  available via `/topology` and `/incidents`, no need to duplicate it
  here.
- **`ecommerce-cloudmart` repo**: `deploy.sh`'s security-ingestion curls
  (Task 12) now send `Authorization: Bearer ${INCIDENT_GATEWAY_API_KEY}`
  — a new env var, same empty-by-default/best-effort treatment as
  `INCIDENT_GATEWAY_URL` since the gateway isn't deployed yet.
- 10 new auth tests (`test_api_auth.py`): fail-closed with no key
  configured, health/ready exempt, missing/wrong/non-Bearer header
  rejected, correct token accepted, and one representative protected
  route from each router group. One implementation wrinkle: `Settings`
  is a frozen dataclass singleton imported by reference everywhere —
  `monkeypatch.setattr()` can't mutate it (raises `FrozenInstanceError`),
  so the "configured" test fixture uses `object.__setattr__` directly
  with explicit teardown instead.
- 12 new incidents-API tests (`test_incidents_api.py`): empty list,
  seeded list, newest-first ordering, status filter, 404 on every
  incident-scoped route for a missing incident, the composite detail
  response verified field-by-field against real seeded
  Observations/Evidence/topology (including that topology is correctly
  *limited* to `affected_services` — seeded an extra unrelated service
  and asserted it's absent), full-object observations/evidence
  endpoints, and timeline ordering. Plus 2 new `/services` tests.
  Updated the 3 existing webhook/topology/ingestion test fixtures to
  bypass auth via a new shared `bypass_auth` fixture
  (`tests/conftest.py`) — those tests are about that endpoint's own
  logic, not auth, which now has its own dedicated coverage. Full suite:
  **200 passed, 19 skipped**.
- **Verified live**: real server, real Postgres, a real
  `GATEWAY_API_KEY`. Confirmed directly against the running server (not
  just tests) that no-auth and wrong-key requests both get 401 and the
  correct token gets 200. Then ran the *entire* pipeline authenticated
  end to end: POSTed a real webhook, waited for the background Context
  Builder to reach `ready_for_investigation`, built topology, then hit
  every new endpoint — `/services`, `/incidents`, `/incidents/{id}`
  (composite shape matched spec section 15's example field-for-field),
  `/incidents/{id}/observations`, `/incidents/{id}/timeline`, and a 404
  for a nonexistent incident. Cross-checked `incidents` and
  `service_topology` via raw `psql`, independent of the app. Zero
  exceptions across the entire server log for the whole session
  (`grep -c "Traceback\|ERROR"` → 0). Stopped both afterward.

### Task 15 — TBD

Per the method sequence, step 22 (deploy this service into k3s — new
`incident-pilot-ecommerce` namespace, per spec section 3) is next: write
the actual Kubernetes manifests (Deployment, Service, the `GATEWAY_API_KEY`
Secret, Postgres + Redis if not already running there, resource
requests/limits sized per the original node-memory guidance) and get this
running in-cluster for real, rather than the scratch-Postgres-plus-local-
uvicorn pattern every task so far has used for live verification. Redis
itself (spec section 11's buffering/caching store) still hasn't been
built at all — worth deciding whether that happens as part of this step
or gets its own. Still outstanding: the two unconfirmed Prometheus
`_METRIC_PROBES`, and Alertmanager's actual routing config.

### Task 15 — K8s manifests + deploy automation (DONE — not yet applied)

**Honest caveat up front**: this sandbox has no live cluster access (same
limitation flagged since Task 6), so nothing below has actually been
`kubectl apply`'d against the real cluster. What's DONE is everything
that *can* be done without that access: a working Dockerfile, complete
manifests, deploy automation, and the strongest local proof available
short of a real cluster. Running `deploy.sh` (or the new GH Actions
workflow) from the EC2 box is the remaining step, and it's genuinely
just running it — nothing here is a draft.

- **`services/observation-gateway/Dockerfile`**: build context must be
  the *repo root*, not the service directory — the app imports
  `shared.models`, which lives outside `services/observation-gateway`,
  so both trees need to land in the image (`docker build -f
  services/observation-gateway/Dockerfile .`). `python:3.11-slim`, runs
  as a non-root user (uid 10001), `PYTHONPATH=/app` recreating the same
  import layout local dev already relies on (pytest run from repo root
  with `app`/`shared` both top-level-importable).
- **`infrastructure/kubernetes/`** (5 files, 10 resources, all validated
  as syntactically correct YAML — `yaml.safe_load_all` over every file):
  - `namespace.yaml` — `incident-pilot-ecommerce`, same label style as
    CloudMart's own `namespace.yaml`.
  - `rbac.yaml` — ServiceAccount + a **namespaced** `Role`/`RoleBinding`
    living in `cloudmart-prod` (not a `ClusterRole`), so the gateway
    gets read access to that one namespace without cluster-wide
    privilege — a `RoleBinding` can bind a ServiceAccount from a
    *different* namespace, which is what makes this possible. Scoped to
    exactly what's actually called today
    (`pods`/`events`/`services`/`deployments`, all `get`/`list` only,
    never write) — `KubernetesClient` has methods for
    nodes/namespaces/configmaps/secrets/replicasets too, but nothing in
    the application calls them yet, so no RBAC is granted for them
    either. Notably: **no `get`/`list` on `secrets` at all**, so the
    gateway's own ServiceAccount can never read Secret values even by
    mistake, regardless of what `list_secret_metadata()`'s code
    discipline promises — this closes the gap that a broader
    `ClusterRole` would have left open (K8s RBAC has no way to grant
    "list only metadata" on Secrets; the only real fix is not granting
    the verb at all when nothing needs it).
  - `configmap.yaml` — the four telemetry base URLs +
    `CLUSTER_NAME`/`DEFAULT_NAMESPACE`, already-verified-live values
    from Task 13.
  - `postgres.yaml` — a plain `Deployment` + PVC, not a `StatefulSet`
    (single-replica non-HA DB, Phase 2A scope — StatefulSet's ordered-
    scaling guarantees buy nothing here). `strategy: Recreate`, not the
    Deployment default `RollingUpdate` — with one `ReadWriteOnce`
    volume, a rolling update would try to schedule the new pod before
    killing the old one and fail to mount. Password/DSN come from a
    `postgres-credentials` Secret this manifest does **not** create —
    bootstrap command documented in the new
    `infrastructure/kubernetes/README.md`, never committed here.
  - `deployment.yaml` — the gateway Deployment + Service. Same
    not-committed-Secret treatment for `gateway-credentials` (the step
    14 API key). Resources sized per the original node-memory guidance
    (Postgres 256Mi/512Mi request/limit, gateway 128Mi/256Mi).
  - `README.md` — the bootstrap commands (both Secrets), what
    `deploy.sh` applies and in what order, and an explicit "what's
    deliberately not here yet" section (Redis, Alertmanager's own config,
    any external exposure — none of those are this task's job).
- **`deploy.sh`** (repo root, mirrors `ecommerce-cloudmart`'s structure
  per spec section 3 point 2): git pull, Gitleaks scan, build/tag/push
  the gateway image, Trivy scan, `kubectl apply` the 5 manifests in
  dependency order, rollout restart + status wait. Then — reusing the
  exact ingestion endpoints from Task 13 rather than building anything
  new — feeds its own Gitleaks/Trivy reports into the gateway it just
  deployed, guarded by `GATEWAY_API_KEY`/best-effort (`|| true`) the same
  way Task 13's `ecommerce-cloudmart` wiring already was. Also updated
  `ecommerce-cloudmart`'s `deploy.sh`: `INCIDENT_GATEWAY_URL` now
  defaults to the real Service DNS name
  (`observation-gateway.incident-pilot-ecommerce.svc.cluster.local:8000`)
  instead of empty — safe to set now that this task defines that name for
  real, and still harmless before the Service exists or before the API
  key is configured (unreachable/401 both silently swallowed by the
  existing `|| true` guards). This closes a gap explicitly flagged as
  open since Task 12.
- **`.github/workflows/deploy.yml`** — structurally identical to
  `ecommerce-cloudmart`'s (same `appleboy/ssh-action`, same
  `EC2_HOST`/`EC2_USER`/`EC2_SSH_KEY` secrets, reused as-is per spec),
  pointing at `~/incident-pilot-ecommerce/deploy.sh`. One addition:
  forwards a new `GATEWAY_API_KEY` repo secret into the remote script's
  environment via the action's `envs` input, needed for the
  self-ingestion step above.
- **Validation performed without live cluster access:**
  - All 5 YAML files parsed successfully via `yaml.safe_load_all`,
    confirming 10 resources with the expected `kind`/`name` — this is
    *not* full API-server schema validation (`kubectl apply
    --dry-run=client` itself tried to reach the cluster's API discovery
    endpoint even with `--validate=false`, and failed the same way every
    other live-cluster call from this sandbox has — genuinely can't be
    done from here).
  - `bash -n` on both `deploy.sh` files.
  - **The strongest substitute available for an actual `docker build`**:
    the Docker daemon isn't reachable in this sandbox either (`docker
    info` hung the same way `docker ps` did earlier in the project — a
    pre-existing sandbox limitation, not new). Instead of skipping
    validation entirely, replicated the *exact* file layout the
    Dockerfile's `COPY` instructions produce (`shared/` and `app/` both
    copied into one directory) in the scratchpad, then ran the *exact*
    `CMD` (`uvicorn app.main:app --host 0.0.0.0 --port 8000`) from
    there with the *exact* env vars `configmap.yaml`/the two Secrets
    would inject, against a real local Postgres. Confirmed: `/health`
    and `/ready` respond, an unauthenticated request to `/topology`
    correctly gets 401, and a fully authenticated webhook POST creates a
    real incident — verified independently via `psql`, not just the
    app's own response. Zero exceptions in the process log. This proves
    the Dockerfile's COPY paths, `PYTHONPATH`, and `CMD` are all correct
    — the only thing an actual `docker build` would additionally prove
    is that the `pip install` layer succeeds in the `python:3.11-slim`
    base image specifically, which wasn't verifiable from here.
- Full app test suite unaffected by this task (infra-only changes):
  **200 passed, 19 skipped**, unchanged from Task 14.

### Task 16 — TBD

Per the method sequence, step 23 (run a controlled fault-injection
scenario end to end against the real deployment) is next — but needs
Task 15's manifests actually applied first, which needs the EC2/k3s
session, not this sandbox. Once applied: bootstrap the two Secrets
(`infrastructure/kubernetes/README.md`), confirm the gateway pod comes up
healthy, wire Alertmanager's receiver config at the real
`gateway-credentials` API key, then break something real in CloudMart
(recommended in the original spec: a bad image tag or crashing env var on
order-service) and confirm the full path fires — Prometheus alert →
Alertmanager → webhook → incident → context collection → visible via the
real in-cluster API, not a local scratch server. Also still open, carried
forward: the two unconfirmed Prometheus `_METRIC_PROBES`, and Redis
(still not built at all).
