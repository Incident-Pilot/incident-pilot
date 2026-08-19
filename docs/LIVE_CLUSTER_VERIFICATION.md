# Verifying the adapters against the real CloudMart cluster

Every adapter built so far (Prometheus, Loki, Tempo, Kubernetes) was written
and tested against **mocked** responses because this dev sandbox has no route
to the CloudMart EC2/k3s box (`kubectl config current-context` here resolves
to an unrelated EKS ARN that doesn't answer DNS). Several assumptions in the
code are flagged as unverified because of this — this doc is the procedure to
close them out, run from the EC2 box itself. Update `docs/PROGRESS.md` with
what you find once you've run these.

## 0. Prerequisites

```bash
ssh <ec2-user>@<ec2-host>
cd ~/incident-pilot-ecommerce   # sibling to ~/ecommerce-cloudmart, per spec section 3
git pull
cd services/observation-gateway
python3 -m venv .venv && source .venv/bin/activate   # if not already set up
pip install -r requirements.txt
kubectl config current-context     # sanity check: should be the k3s cluster, not something else
kubectl get nodes                  # sanity check: should return the single t3.medium node
```

## 1. Kubernetes adapter — no port-forward needed

Runs directly against the in-cluster/default kubeconfig context:

```bash
PYTHONPATH=. python scripts/live_check_kubernetes.py
```

**What to check:**
- `list_pods`/`list_deployments`/`list_events`/`get_nodes` should all print
  `AVAILABLE`, not `UNAVAILABLE`/`TIMEOUT`.
- Pod/deployment/event data should show the real `cloudmart-prod` services
  (`frontend`, `product-service`, `order-service`, `user-service`,
  `notification-service`).
- **Design decision flagged in Task 5** (`docs/PROGRESS.md`): `list_events()`
  uses the classic `v1.Event` API, not `events.k8s.io/v1`. If you specifically
  need the newer API's richer fields, say so and this gets changed.

## 2. Prometheus adapter

```bash
kubectl port-forward -n observability svc/kube-prom-kube-prometheus-prometheus 9090:9090 &
PYTHONPATH=. python scripts/live_check_prometheus.py
```

**What to check:**
- `status: AVAILABLE` with at least one series for the trivial `up` query.
- **This is also where you confirm the biggest open question from the
  Context Builder (step 10)**: the four PromQL probes in
  `app/context/incident_context_builder.py` (`_METRIC_PROBES`) assume
  standard cAdvisor/kube-state-metrics/Traefik metric names
  (`kube_pod_container_status_restarts_total`,
  `container_cpu_usage_seconds_total`,
  `container_memory_working_set_bytes`, `traefik_service_requests_total`)
  and a `pod=~"{service}.*"` naming convention. Confirm these actually exist
  and return data for a real service, e.g.:
  ```bash
  curl -s 'http://localhost:9090/api/v1/query?query=up{namespace="cloudmart-prod"}' | python3 -m json.tool
  curl -s 'http://localhost:9090/api/v1/label/__name__/values' | python3 -m json.tool | grep -i traefik
  curl -s 'http://localhost:9090/api/v1/query?query=kube_pod_container_status_restarts_total{namespace="cloudmart-prod"}' | python3 -m json.tool
  ```
  If a metric name is wrong for this cluster, it's a one-line fix in
  `_METRIC_PROBES` — nothing else depends on the exact string.
- Also worth checking now, from the original Step 0 ask (not yet confirmed):
  whether **application-level** metrics exist at all, or only
  infrastructure-level ones (cAdvisor/kube-state-metrics/Traefik/node-exporter)
  — i.e. do `product-service`/`order-service`/etc. expose their own
  `/metrics`? The spec's own inspection said no `/metrics` endpoint exists in
  the app code, so this should come back empty/absent — confirming that is
  itself useful (it means the Context Builder's metrics are necessarily
  infra-level only, not app-level, which is worth knowing before anyone
  expects otherwise).

## 3. Loki adapter

```bash
kubectl port-forward -n observability svc/loki 3100:3100 &
PYTHONPATH=. python scripts/live_check_loki.py
```

**What to check:**
- `status: AVAILABLE` with entries returned for `{namespace="cloudmart-prod"}`.
- **The flagged unknown**: do `service`/`pod`/`container` come back populated
  on real entries, or `None`? `loki_adapter.py`'s label-candidate lists
  (`_SERVICE_LABEL_CANDIDATES` etc.) guess at Promtail's actual label
  convention. If `service`/`pod` print `None` for real log lines, add
  the real label key to those candidate lists — the raw label dict is always
  printed too, so you can see the actual keys directly:
  ```bash
  # look at the raw `labels=` dict printed by the script for the real keys
  ```

## 4. Tempo adapter

```bash
kubectl port-forward -n observability svc/tempo 3200:3200 &

# Generate a real trace first if none exist yet — hit the app a few times
# through its ingress/NodePort, e.g.:
curl http://<cloudmart-frontend-url>/  # a few times, to generate order-service traffic

# Then either grab a trace ID from Grafana's Tempo Explore view, or let the
# script fall back to a search:
PYTHONPATH=. python scripts/live_check_tempo.py
# or, with a known trace ID:
PYTHONPATH=. TRACE_ID=<real-trace-id> python scripts/live_check_tempo.py
```

**What to check — this is the highest-value unknown of the four:**
- **Does application-level tracing exist at all?** The original spec doc
  claimed all four backend services are instrumented with OpenTelemetry, but
  a separate inspection of the actual CloudMart app code found **no
  OpenTelemetry SDK anywhere** and no `/metrics` endpoints — only `/health`
  and `/ready`. If that inspection is right, Tempo may have **no traces to
  find at all**, regardless of whether the adapter code is correct. Run the
  search first (no `TRACE_ID`) and see if `traces found: 0` even after
  generating real app traffic — if so, that's the actual finding to report,
  not an adapter bug.
- If traces *do* come back: does `parse_spans()` return non-empty spans with
  `service` populated? If `spans parsed: 0` despite `result.data` clearly
  containing trace data, the Jaeger-shaped-response assumption documented in
  `tempo_adapter.py` is wrong for this Tempo version, and `parse_spans()`
  needs an OTLP-JSON code path instead.
- **Also check Tempo's own health while you're in there** (flagged in the
  original scenario doc as a known live issue — confirm it's still true):
  ```bash
  kubectl get pod tempo-0 -n observability
  kubectl describe pod tempo-0 -n observability | tail -30
  ```
  Confirm the restart count/pattern and whether liveness/readiness probe
  failures are still occurring — this is what the Context Builder's
  "treat Tempo timeout as normal, not an error" resilience path
  (`app/context/incident_context_builder.py`, spec section 13) was built to
  tolerate, so it's worth knowing if that's still actually happening.

## 5. Node headroom (from the original Step 0 ask, still unconfirmed)

```bash
kubectl top nodes
kubectl get pods -A -o wide | grep -v Running   # anything Evicted/Pending/CrashLoopBackOff right now?
```

Report back current memory % used — this determines whether it's safe to
schedule the gateway + Postgres + Redis onto this node later (step 15), per
the original sizing guidance (gateway ~128–256Mi, Postgres ~256–512Mi, Redis
~64–128Mi with `maxmemory` capped). If the node is already past ~80% memory,
stop and flag it rather than deploying blind.

## 6. What to report back

For each of the four adapters: AVAILABLE or not, and whether the specific
flagged assumption held (label names, response shape, metric names, whether
app-level traces exist at all). Plus current node memory %, and Tempo's live
restart status. This closes out the "not yet verified" caveat that's been
carried in `docs/PROGRESS.md` since Task 2, and is the last gate before the
Context Builder's `_METRIC_PROBES`/log query/trace search in
`app/context/incident_context_builder.py` can be trusted to return real data
in production rather than just failing closed (which is what they've been
proven to do safely so far, per the resilience tests in
`test_incident_context_builder.py`).
