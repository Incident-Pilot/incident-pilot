# First deploy — Observation Gateway to the real k3s cluster

This is the linear, first-time walkthrough. `infrastructure/kubernetes/README.md`
has the reference details (what each manifest does, why); this doc is the
order to actually run things in, plus verification and troubleshooting at
each step. Run everything below from an SSH session on the EC2 box.

## 0. Check node headroom before deploying anything new

This is the same check flagged since the very start of this project, and
Task 13's live verification confirmed the concern was real (disk, not
memory, turned out to be the actual eviction trigger):

```bash
kubectl top nodes
df -h
kubectl get pods -A --field-selector=status.phase=Failed -o name | wc -l
```

If memory is past ~80% or disk is tight, stop here and clean up first —
Task 13's `docs/PROGRESS.md` entry has the exact `kubectl delete pods
--field-selector=status.phase=Failed` / `...=Succeeded` commands. Don't
deploy Postgres + the gateway onto a node that's already under pressure.

## 1. Get the repo onto the box

```bash
cd ~
# if this is the first time:
git clone <incident-pilot-ecommerce repo URL> incident-pilot-ecommerce
cd incident-pilot-ecommerce

# if it's already cloned (this session likely already has it, given the
# commits already on origin/main from the live-verification work):
cd ~/incident-pilot-ecommerce && git pull origin main
```

Confirm it's a sibling of `~/ecommerce-cloudmart`, per the original repo
layout decision — `ls ~` should show both.

## 2. Confirm the tools deploy.sh needs are there

`ecommerce-cloudmart`'s `deploy.sh` already uses all of these successfully
on this box, so this should just confirm, not install anything:

```bash
docker --version
kubectl version --client
gitleaks version
trivy --version
kubectl config current-context   # should be the k3s cluster, not something else
```

## 3. Bootstrap the two Secrets (one-time only, per cluster)

Neither is in the repo — by design, per spec section 12 ("never commit
credentials"). `deployment.yaml`/`postgres.yaml` reference both via
`secretKeyRef`; if either is missing, those pods will sit in
`CreateContainerConfigError` until you create it.

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

**Save the `GATEWAY_API_KEY` value** — you'll reuse it in step 4 below,
in `ecommerce-cloudmart`'s own `deploy.sh` (`INCIDENT_GATEWAY_API_KEY`,
already wired up in Task 12/14), and eventually in Alertmanager's
receiver config once that's connected.

## 4. Run the deploy

```bash
cd ~/incident-pilot-ecommerce
chmod +x deploy.sh   # first run only
export GATEWAY_API_KEY="<the value you saved in step 3>"
./deploy.sh
```

What it does, in order: Gitleaks scan → build/tag/push the gateway image
to `localhost:5000` → Trivy scan → `kubectl apply` the 5 manifests
(namespace, rbac, configmap, postgres, deployment) → rollout restart +
wait → feed its own two scan reports into the gateway it just deployed
(this last step needs `GATEWAY_API_KEY` exported, or it silently no-ops —
harmless either way, but you won't see those findings show up in
`/incidents` if you skip it).

## 5. Verify the pods actually came up

```bash
kubectl get pods -n incident-pilot-ecommerce
kubectl get pods -n incident-pilot-ecommerce -w   # Ctrl-C once both are Running/Ready
```

If something's wrong:

| Symptom | Likely cause | Check |
|---|---|---|
| `CreateContainerConfigError` | A Secret key name doesn't match, or step 3 was skipped | `kubectl describe pod ... \| grep -A5 Events`, confirm secret keys are exactly `password`/`dsn`/`api-key` |
| `ImagePullBackOff` on the gateway | `docker push` in step 4 didn't actually reach `localhost:5000` | `curl http://localhost:5000/v2/incident-pilot-ecommerce/observation-gateway/tags/list` on the EC2 box |
| Postgres `CrashLoopBackOff` | PVC didn't bind, or password mismatch between the two `postgres-credentials` keys | `kubectl get pvc -n incident-pilot-ecommerce`, `kubectl logs -n incident-pilot-ecommerce deployment/postgres` |
| Gateway `CrashLoopBackOff` | `POSTGRES_DSN` malformed/unreachable, or a real Python import error | `kubectl logs -n incident-pilot-ecommerce deployment/observation-gateway` |

```bash
kubectl logs -n incident-pilot-ecommerce deployment/observation-gateway --tail=50
kubectl logs -n incident-pilot-ecommerce deployment/postgres --tail=20
```

## 6. Smoke-test from inside the cluster

The gateway is `ClusterIP`-only by design (no external exposure asked
for). Easiest path from the EC2 box itself:

```bash
kubectl port-forward -n incident-pilot-ecommerce svc/observation-gateway 8000:8000 &

curl -s http://localhost:8000/health
curl -s http://localhost:8000/ready
curl -s -o /dev/null -w "no-auth status: %{http_code}\n" http://localhost:8000/topology   # expect 401

curl -s -H "Authorization: Bearer $GATEWAY_API_KEY" http://localhost:8000/topology | python3 -m json.tool
curl -s -H "Authorization: Bearer $GATEWAY_API_KEY" http://localhost:8000/services | python3 -m json.tool
```

`GET /topology` here is the first time it runs against the **real**
Kubernetes API and the **real** Tempo from inside the cluster — every
prior "live" verification of this project ran from a sandbox with a
scratch local Postgres and no real backends. Confirm it comes back with
real `cloudmart-prod` services, not just the static seed.

## 7. Send a real webhook and confirm the full pipeline, for real

```bash
curl -s -X POST http://localhost:8000/webhooks/alertmanager \
  -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "firing",
    "groupLabels": {"alertname": "HighHTTPErrorRate", "namespace": "cloudmart-prod"},
    "alerts": [{"status":"firing","labels":{"alertname":"HighHTTPErrorRate","severity":"critical","namespace":"cloudmart-prod","service":"order-service"},"annotations":{},"startsAt":"2026-08-20T09:30:00Z","endsAt":"0001-01-01T00:00:00Z"}]
  }' | python3 -m json.tool
```

Grab the `incident_id` from the response, then poll until context
collection finishes (same pattern used throughout this project's own
live verification — a plain loop is more robust over SSH than `watch`):

```bash
INCIDENT_ID="<id from above>"
for i in 1 2 3 4 5 6 7 8; do
  PHASE=$(curl -s -H "Authorization: Bearer $GATEWAY_API_KEY" \
    "http://localhost:8000/incidents/$INCIDENT_ID" | python3 -c "import json,sys; print(json.load(sys.stdin)['current_phase'])")
  echo "[$(date +%H:%M:%S)] phase=$PHASE"
  [ "$PHASE" = "ready_for_investigation" ] && break
  sleep 5
done
```

Once it reaches `ready_for_investigation`:

```bash
curl -s -H "Authorization: Bearer $GATEWAY_API_KEY" http://localhost:8000/incidents/$INCIDENT_ID | python3 -m json.tool
```

This is the real test of everything built in Tasks 9-14 — real metrics
from real Prometheus, real logs from real Loki, real traces from real
Tempo (or a real `unavailable`/`timeout` if Tempo happens to be
mid-restart, which is fine — that's the resilience path working as
designed, not a bug), real K8s events, and real deployment context if
`ecommerce-cloudmart`'s `deploy.sh` has stamped the annotations on
`order-service` at least once since Task 12 landed there.

When done: `kill %1` to stop the port-forward.

## 8. Wire up automated deploys (optional, once step 4-7 worked manually)

In the `incident-pilot-ecommerce` GitHub repo settings, add these Actions
secrets:
- `EC2_HOST` / `EC2_USER` / `EC2_SSH_KEY` — same values already used by
  `ecommerce-cloudmart`'s workflow, reusable as-is (not app-specific)
- `GATEWAY_API_KEY` — the value from step 3

From then on, a push to `main` runs `.github/workflows/deploy.yml`, which
SSHes in and runs the same `deploy.sh` you just ran manually.

## What this does NOT do

- **Alertmanager's receiver config** still needs to be pointed at this
  webhook with the real `GATEWAY_API_KEY` — that's a change to
  Alertmanager's own config in the `observability` namespace, not
  something `deploy.sh` touches.
- **Redis** isn't deployed — no application code uses it yet (spec
  section 11's buffering/caching store was never built), so there's
  nothing to deploy.
- **The two still-unconfirmed Prometheus `_METRIC_PROBES`**
  (`cpu_usage_seconds`, `memory_working_set_bytes`) — worth checking now
  that the Context Builder is running for real against real incidents;
  see `docs/LIVE_CLUSTER_VERIFICATION.md` section 2 for the exact curl
  commands.
