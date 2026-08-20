"""
Observation Gateway configuration.

Defaults below are the actual ClusterIP service DNS names verified on
the CloudMart k3s cluster (2026-08-13, `kubectl get svc -n observability
-o wide`). They only resolve from *inside* the cluster, which is where
this service is intended to run (spec section 39). Override via env vars
for local/port-forwarded testing.

Auth: live verification (2026-08-20, docs/LIVE_CLUSTER_VERIFICATION.md)
made real calls against Prometheus, Loki, Tempo, and the Kubernetes API
via these endpoints/ClusterIPs and every one came back `available` — no
401/403 encountered. Treating them as unauthenticated is confirmed
correct in practice, not just an untested assumption; revisit only if
that ever changes (e.g. an auth proxy gets added in front of one later).
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

    # spec section 1: the CloudMart application namespace. GET /topology
    # (step 11) builds its graph for this namespace since topology isn't
    # scoped to a single incident the way context collection is.
    default_namespace: str = os.getenv("DEFAULT_NAMESPACE", "cloudmart-prod")

    # spec section 12: "do not expose this unauthenticated." Sourced from
    # a Kubernetes Secret mounted as an env var (step 15's manifests) —
    # never a real value in this repo. Empty by default, which
    # app/api/auth.py treats as "reject everything" (503), not "allow
    # everything" — a missing Secret must fail closed, not open.
    api_key: str = os.getenv("GATEWAY_API_KEY", "")


settings = Settings()
