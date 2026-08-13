"""
Observation Gateway configuration.

Defaults below are the actual ClusterIP service DNS names verified on
the CloudMart k3s cluster (2026-08-13, `kubectl get svc -n observability
-o wide`). They only resolve from *inside* the cluster, which is where
this service is intended to run (spec section 39). Override via env vars
for local/port-forwarded testing.

NOT YET VERIFIED: whether any auth sits in front of these query APIs.
`kubectl get secrets -n observability` showed only TLS/webhook certs for
the Prometheus operator's admission webhook and Grafana's admin login —
nothing indicating basic auth on the Prometheus/Loki/Tempo query
endpoints themselves. Treating them as unauthenticated for now; revisit
if a live call comes back 401/403.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    prometheus_base_url: str = os.getenv(
        "PROMETHEUS_BASE_URL",
        "http://kube-prom-kube-prometheus-prometheus.observability.svc.cluster.local:9090",
    )
    loki_base_url: str = os.getenv(
        "LOKI_BASE_URL",
        "http://loki.observability.svc.cluster.local:3100",
    )
    tempo_base_url: str = os.getenv(
        "TEMPO_BASE_URL",
        "http://tempo.observability.svc.cluster.local:3200",
    )
    http_timeout_seconds: float = float(os.getenv("HTTP_TIMEOUT_SECONDS", "5.0"))


settings = Settings()
