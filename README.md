# IncidentPilot

Agentic AI Incident Response and SRE platform for the CloudMart k3s
environment. Currently in **Phase 2A**: Observation Gateway + Incident
Ingestion + Incident Context Builder (deterministic, no LLM reasoning
yet — see `docs/PROGRESS.md` for the full plan and current status).

## What it's for

CloudMart (`ecommerce-cloudmart`) runs on a single-node k3s cluster with a
Prometheus/Loki/Tempo/Grafana observability stack in front of it, but
nothing turns an alert into an *investigable incident*. When something
fires, an engineer still has to manually go pull metrics from Prometheus,
logs from Loki, traces from Tempo, pod/deployment state from the
Kubernetes API, and recent deploy/security-scan history — by hand, across
four different tools.

IncidentPilot's Observation Gateway automates that correlation step:

- Receives Alertmanager webhooks (and Gitleaks/Trivy scan results from
  CloudMart's own `deploy.sh`) and normalizes them into a canonical
  `Observation` model.
- Deterministically correlates related alerts into a single `Incident`
  (same namespace/service, within a configurable time window) instead of
  spawning duplicates for every firing alert.
- Builds **Incident Context**: pulls the relevant window of metrics
  (Prometheus), logs (Loki), traces (Tempo), Kubernetes events/pod state,
  and recent deployment/security-scan history for the affected
  service — all in parallel, degrading gracefully (`unavailable`/
  `timeout`, never a hard failure) if any one source is down.
- Exposes all of this over a small authenticated REST API so a human (and
  eventually an LLM-driven investigation agent — later phases) can pull
  up "everything relevant to this incident" in one call instead of four.
- Builds a service topology view (`GET /topology`) from live Kubernetes
  state, so context includes what a failing service actually talks to.

Phase 2A intentionally stops at deterministic ingestion/correlation/
context-building — no LLM reasoning, no automated remediation. That's the
groundwork later phases build the actual "agentic" investigation and
response loop on top of.

## Architecture

```
                     ┌─────────────────────────────────────────┐
                     │      cloudmart-prod namespace            │
                     │  (CloudMart e-commerce microservices)    │
                     └───────────────┬───────────────────────────┘
                                      │ scraped/queried by
                                      ▼
   ┌─────────────────────────────────────────────────────────────┐
   │                  observability namespace                     │
   │   Prometheus · Loki · Tempo · Grafana · Alertmanager          │
   └───────┬───────────────┬───────────────┬───────────────────────┘
           │ metrics       │ logs          │ traces          │ alert fires
           ▼               ▼               ▼                 ▼
   ┌─────────────────────────────────────────────────────────────┐
   │        incident-pilot-ecommerce namespace                     │
   │                                                                │
   │   POST /webhooks/alertmanager  ─────────────┐                 │
   │   POST /ingest/gitleaks|trivy  ─────────────┤                 │
   │                                              ▼                 │
   │                                    ┌─────────────────────┐    │
   │                                    │  Observation Gateway │    │
   │                                    │      (FastAPI)       │    │
   │                                    │                       │    │
   │  Prometheus/Loki/Tempo adapters ◄──┤  Incident Correlator │    │
   │  Kubernetes adapter (RBAC:        │  Incident Context     │    │
   │    get/list pods/events/          │    Builder             │    │
   │    services/deployments) ◄────────┤  Service Topology      │    │
   │                                    │    Builder             │    │
   │                                    └──────────┬────────────┘    │
   │                                               │                 │
   │                          GET /incidents, /topology, /services   │
   │                                               │                 │
   │                                               ▼                 │
   │                                       ┌──────────────┐          │
   │                                       │  PostgreSQL   │          │
   │                                       │ (1 replica,   │          │
   │                                       │  PVC-backed)  │          │
   │                                       └──────────────┘          │
   └─────────────────────────────────────────────────────────────┘
```

**Service:** `services/observation-gateway/` — a single FastAPI app.

- **Ingestion**: `POST /webhooks/alertmanager` (Alertmanager firing/
  resolved alerts), `POST /ingest/gitleaks`, `POST /ingest/trivy`
  (security scan results, pushed by CloudMart's and this repo's own
  `deploy.sh`). All normalized into a canonical `Observation`
  (`shared/models/`).
- **Correlation**: a deterministic `IncidentCorrelator` merges a new
  observation into an existing open `Incident` if namespace/service
  overlap and it's within `CORRELATION_WINDOW_MINUTES` (default 15);
  otherwise it opens a new one.
- **Context building**: `IncidentContextBuilder` fans out to four
  collectors — Prometheus, Loki, Tempo, Kubernetes — over the incident's
  `CONTEXT_WINDOW_MINUTES` lookback, plus a deployment-context collector
  (recent rollout/annotation history) and security context (recent
  Gitleaks/Trivy findings for the affected service). Every collector
  returns `AVAILABLE`/`UNAVAILABLE`/`TIMEOUT` — a source outage degrades
  the incident to partial context, it never fails the request.
- **Topology**: `GET /topology` / `GET /services` build a live service
  graph from the Kubernetes adapter for `DEFAULT_NAMESPACE`
  (`cloudmart-prod`).
- **API**: `GET /incidents`, `GET /incidents/{id}` (+ `/observations`,
  `/evidence`, `/source-status`, `/timeline` sub-resources), `PATCH
  /incidents/{id}/status`. Every route except `/health`/`/ready` requires
  a bearer token (`GATEWAY_API_KEY`) via `require_api_key`.
- **Persistence**: PostgreSQL when `POSTGRES_DSN` is set (production);
  falls back to in-memory stores otherwise (local dev/tests).
- **RBAC**: the gateway's ServiceAccount gets a cluster-scoped, read-only
  `ClusterRole` (`get`/`list` on `pods`/`events`/`services`/
  `deployments` only — no write verbs, no `secrets` access at all).

See `docs/PROGRESS.md` for the task-by-task build log and design
decisions, and `infrastructure/kubernetes/README.md` for what each
manifest does and why.

## Repo layout

```
incidentpilot/
├── services/observation-gateway/   # FastAPI service (Phase 2A core)
├── shared/models/                  # Canonical Observation/Incident/Evidence schemas
├── infrastructure/kubernetes/      # k3s manifests
├── infrastructure/observability/   # Helm values for the shared Prometheus/Loki/Tempo/Grafana stack
├── deploy.sh                       # Build/scan/push/apply, run on the EC2 box
└── docs/                           # PROGRESS.md, DEPLOY_TO_EC2.md, LIVE_CLUSTER_VERIFICATION.md
```

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest shared/tests -v
```

For the Observation Gateway service itself:

```bash
cd services/observation-gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r ../../requirements-dev.txt   # pytest, etc.
python -m pytest -v                          # runs against in-memory stores, no cluster/DB needed

uvicorn app.main:app --reload --port 8000    # run it locally
curl http://localhost:8000/health
```

Without `POSTGRES_DSN`/`GATEWAY_API_KEY` set, the app runs against
in-memory stores with auth disabled-by-fail-closed (every non-health
route returns 503 until `GATEWAY_API_KEY` is set) — this is what the test
suite exercises. Real Prometheus/Loki/Tempo/Kubernetes access only works
from inside the cluster (or via `kubectl port-forward` — see
`services/observation-gateway/scripts/live_check_*.py` for one-off live
checks against the real cluster).

## Deploying to the shared EC2/k3s box (alongside ecommerce-cloudmart)

IncidentPilot deploys as its **own namespace** (`incident-pilot-ecommerce`)
on the **same** single-node k3s cluster and EC2 host that already runs
`ecommerce-cloudmart` (namespace `cloudmart-prod`) and the shared
observability stack (namespace `observability`). It reads
Prometheus/Loki/Tempo/Kubernetes from that existing stack — it doesn't
run its own copy of any of it — and it reuses the same Docker registry
(`localhost:5000`), the same CI tooling (Gitleaks/Trivy already installed
on the box for `ecommerce-cloudmart`'s own `deploy.sh`), and the same
GitHub Actions SSH secrets (`EC2_HOST`/`EC2_USER`/`EC2_SSH_KEY`).

Full step-by-step walkthrough (with verification and a troubleshooting
table): **`docs/DEPLOY_TO_EC2.md`**. Summary:

1. **Prerequisites on the EC2 box** — already true if `ecommerce-cloudmart`
   is deployed there: Docker, `kubectl` pointed at the k3s cluster, a
   local registry at `localhost:5000`, `gitleaks`, `trivy`. Clone this repo
   as a **sibling** of `~/ecommerce-cloudmart`, i.e. `~/incident-pilot-ecommerce`.

   ```bash
   cd ~
   git clone <this repo URL> incident-pilot-ecommerce
   ```

2. **Check node headroom first** — this cluster already runs the
   observability stack + CloudMart's services on one node:

   ```bash
   kubectl top nodes
   df -h
   ```

3. **Bootstrap the two Secrets** (one-time per cluster; never committed to
   this repo):

   ```bash
   kubectl create namespace incident-pilot-ecommerce

   POSTGRES_PASSWORD="$(openssl rand -hex 24)"
   kubectl create secret generic postgres-credentials \
     -n incident-pilot-ecommerce \
     --from-literal=password="${POSTGRES_PASSWORD}" \
     --from-literal=dsn="postgresql://incidentpilot:${POSTGRES_PASSWORD}@postgres.incident-pilot-ecommerce.svc.cluster.local:5432/incidentpilot"

   GATEWAY_API_KEY="$(openssl rand -hex 32)"
   echo "SAVE THIS — gateway API key: ${GATEWAY_API_KEY}"
   kubectl create secret generic gateway-credentials \
     -n incident-pilot-ecommerce \
     --from-literal=api-key="${GATEWAY_API_KEY}"
   ```

   Save `GATEWAY_API_KEY` — you'll reuse it for this repo's own
   `deploy.sh` and for `ecommerce-cloudmart`'s `deploy.sh`
   (`INCIDENT_GATEWAY_API_KEY`, for its Gitleaks/Trivy ingestion), and
   eventually for Alertmanager's receiver config.

4. **Run the deploy**:

   ```bash
   cd ~/incident-pilot-ecommerce
   export GATEWAY_API_KEY="<value saved above>"
   ./deploy.sh
   ```

   This scans the repo (Gitleaks), builds and pushes the
   `observation-gateway` image to the shared `localhost:5000` registry,
   scans the image (Trivy), applies the five manifests in
   `infrastructure/kubernetes/` (namespace, rbac, configmap, postgres,
   deployment), restarts the Deployment, waits for rollout, then feeds
   its own scan reports into the gateway it just deployed.

5. **Verify**:

   ```bash
   kubectl get pods -n incident-pilot-ecommerce
   kubectl port-forward -n incident-pilot-ecommerce svc/observation-gateway 8000:8000 &
   curl -s http://localhost:8000/health
   curl -s -H "Authorization: Bearer $GATEWAY_API_KEY" http://localhost:8000/topology
   ```

   The gateway is `ClusterIP`-only (no external exposure) — reachable from
   inside the cluster (Alertmanager, `deploy.sh`) and via `kubectl
   port-forward` for manual checks, same pattern as the rest of this stack.

6. **Automate it (optional)**: add `EC2_HOST`/`EC2_USER`/`EC2_SSH_KEY`
   (reused as-is from `ecommerce-cloudmart`'s workflow) and
   `GATEWAY_API_KEY` as GitHub Actions secrets on this repo. From then on,
   a push to `main` runs `.github/workflows/deploy.yml`, which SSHes into
   the same EC2 box and runs `deploy.sh`.

**Still manual after this**: pointing Alertmanager's receiver config (in
the `observability` namespace) at `POST /webhooks/alertmanager` with the
real `GATEWAY_API_KEY` — that's a config change to Alertmanager itself,
not something either repo's `deploy.sh` touches.
