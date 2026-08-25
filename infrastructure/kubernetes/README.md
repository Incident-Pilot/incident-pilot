# Deploying the Observation Gateway

## One-time bootstrap (run once, before the first `deploy.sh`)

Two Secrets are required and deliberately **not** committed to this repo
(spec section 12: "never commit credentials"). Create them directly on the
EC2/k3s box:

```bash
kubectl create namespace incident-pilot-ecommerce

# Postgres — the `dsn` key is what the gateway actually reads
# (POSTGRES_DSN); `password` is what the postgres:16-alpine container
# itself reads to initialize. Keep them in sync.
POSTGRES_PASSWORD="$(openssl rand -hex 24)"
kubectl create secret generic postgres-credentials \
  -n incident-pilot-ecommerce \
  --from-literal=password="${POSTGRES_PASSWORD}" \
  --from-literal=dsn="postgresql://incidentpilot:${POSTGRES_PASSWORD}@postgres.incident-pilot-ecommerce.svc.cluster.local:5432/incidentpilot"

# Gateway API key — spec section 12's bearer token. Save this value;
# you'll need it for:
#   - Alertmanager's webhook receiver config (once that's wired up)
#   - this repo's own deploy.sh (GATEWAY_API_KEY, for the security-findings
#     self-ingestion at the end of the script)
#   - the ecommerce-cloudmart repo's deploy.sh (INCIDENT_GATEWAY_API_KEY,
#     for its Gitleaks/Trivy ingestion curls, added in step 12/14)
GATEWAY_API_KEY="$(openssl rand -hex 32)"
echo "Gateway API key (save this): ${GATEWAY_API_KEY}"
kubectl create secret generic gateway-credentials \
  -n incident-pilot-ecommerce \
  --from-literal=api-key="${GATEWAY_API_KEY}"
```

## Deploying

```bash
bash deploy.sh
```

Or via the GitHub Actions workflow (`.github/workflows/deploy.yml`) on push
to `main` — set the `GATEWAY_API_KEY` repo secret (the same value from the
bootstrap step above) alongside the `EC2_HOST`/`EC2_USER`/`EC2_SSH_KEY`
secrets already used by `ecommerce-cloudmart`'s workflow (reused as-is).

`deploy.sh` applies, in order: `namespace.yaml`, `rbac.yaml`,
`configmap.yaml`, `postgres.yaml`, `deployment.yaml` — then restarts the
gateway Deployment and waits for rollout. It does **not** touch
`cloudmart-prod` or `observability` beyond the read-only `Role`/
`RoleBinding` `rbac.yaml` adds inside `cloudmart-prod` (see that file's
own comment for why this is scoped as tightly as the code actually needs
today, not as broadly as the adapter class technically could support).

## What's deliberately not here yet

- **Redis** (spec section 11's buffering/caching store) — no application
  code uses Redis at all yet, so there's no manifest for it either;
  deploying an unused pod would just be infrastructure without a purpose.
  Add `redis.yaml` when that code lands.
- **Alertmanager's receiver config** pointing at this gateway's webhook —
  that's a change to Alertmanager's own config (in the `observability`
  namespace), not something this repo's manifests apply.
- **Ingress/TLS/external exposure** — the gateway is `ClusterIP`-only,
  reachable from inside the cluster (Alertmanager, deploy.sh's
  self-ingestion curls) but not from outside it. Nothing in the spec asks
  for external access to this service.

## Verifying after a deploy

```bash
kubectl get pods -n incident-pilot-ecommerce
kubectl logs -n incident-pilot-ecommerce deployment/observation-gateway
kubectl exec -n incident-pilot-ecommerce deployment/observation-gateway -- \
  curl -s http://localhost:8000/health
```
