#!/bin/bash
set -e
cd ~/incident-pilot-ecommerce
git pull origin main

mkdir -p reports

echo "=== Gitleaks: scanning repo for secrets ==="
gitleaks detect --source . --report-format json --report-path reports/gitleaks-report.json --exit-code 0

echo "=== Building observation-gateway image ==="
# Build context is the repo root, not services/observation-gateway — see
# the Dockerfile's own comment for why (it needs shared/ too).
docker build -t incident-pilot-ecommerce/observation-gateway:v1 -f services/observation-gateway/Dockerfile .
docker tag incident-pilot-ecommerce/observation-gateway:v1 localhost:5000/incident-pilot-ecommerce/observation-gateway:v1
docker push localhost:5000/incident-pilot-ecommerce/observation-gateway:v1

echo "=== Trivy: scanning observation-gateway image ==="
trivy image --format json --output reports/trivy-observation-gateway.json --exit-code 0 localhost:5000/incident-pilot-ecommerce/observation-gateway:v1

kubectl apply -f infrastructure/kubernetes/namespace.yaml
kubectl apply -f infrastructure/kubernetes/rbac.yaml
kubectl apply -f infrastructure/kubernetes/configmap.yaml
kubectl apply -f infrastructure/kubernetes/postgres.yaml
kubectl apply -f infrastructure/kubernetes/deployment.yaml

kubectl rollout restart deployment observation-gateway -n incident-pilot-ecommerce
kubectl rollout status deployment/observation-gateway -n incident-pilot-ecommerce --timeout=120s

# Feed this deploy's own security scans into the gateway it just deployed
# — reuses the exact ingestion endpoints built in step 13. GATEWAY_API_KEY
# must match the `gateway-credentials` Secret's api-key
# (infrastructure/kubernetes/deployment.yaml's bootstrap comment). Both
# best-effort (`|| true`): a scan with nothing to report, or the very
# first deploy racing the rollout above, must never fail this script.
GATEWAY_URL="${GATEWAY_URL:-http://observation-gateway.incident-pilot-ecommerce.svc.cluster.local:8000}"
GATEWAY_API_KEY="${GATEWAY_API_KEY:-}"

if [ -n "$GATEWAY_API_KEY" ] && [ -s reports/gitleaks-report.json ]; then
  curl -sf -X POST "$GATEWAY_URL/ingest/gitleaks" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${GATEWAY_API_KEY}" \
    --data @reports/gitleaks-report.json || true
fi
if [ -n "$GATEWAY_API_KEY" ] && [ -s reports/trivy-observation-gateway.json ]; then
  curl -sf -X POST "$GATEWAY_URL/ingest/trivy?service=observation-gateway" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${GATEWAY_API_KEY}" \
    --data @reports/trivy-observation-gateway.json || true
fi
