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

    # spec section 4's illustrative Observation carries cluster: "cloudmart-k3s".
    # No live API surfaces this name anywhere, so it is a static config value.
    cluster_name: str = os.getenv("CLUSTER_NAME", "cloudmart-k3s")

    # Empty by default -> main.py's lifespan keeps the in-memory stores
    # (dev/test behavior unchanged). Set to a real DSN (step 15's k8s
    # manifests source this from a Secret, never commit a real one here)
    # to switch the app over to Postgres-backed persistence on startup.
    postgres_dsn: str = os.getenv("POSTGRES_DSN", "")

    # spec section 7: deterministic incident correlation window. A firing
    # alert only merges into an existing OPEN incident if that incident's
    # namespace/service overlap AND it was last updated within this many
    # minutes — otherwise a new incident is created. Same 15-minute
    # default the spec suggests for the Context Builder's lookback window
    # (section 9), reused here since both express "still the same event."
    correlation_window_minutes: float = float(os.getenv("CORRELATION_WINDOW_MINUTES", "15"))

    # spec section 9's suggested default lookback for the Incident Context
    # Builder: how far back from "now" to pull metrics/logs/traces/events.
    context_window_minutes: float = float(os.getenv("CONTEXT_WINDOW_MINUTES", "15"))


settings = Settings()
