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
- [ ] 7. Implement Loki adapter
- [ ] 8. Test Loki adapter
- [ ] 9. Implement Tempo adapter
- [ ] 10. Test Tempo adapter
- [ ] 11. Implement Kubernetes adapter
- [ ] 12. Test Kubernetes adapter
- [ ] 13. Implement Alertmanager webhook
- [ ] 14. Implement normalization
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

### Task 3 — TBD

Candidates per the method sequence: verify the live_check_prometheus.py
script against your real cluster, then implement + test the Loki adapter
(steps 7-8). Loki's actual log label conventions (e.g. does `namespace`
label exist, what's the container/pod label name) haven't been verified
yet either — will need a quick `kubectl exec` / port-forward + curl check
similar to what we just did for Prometheus.
