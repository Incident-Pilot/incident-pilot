"""
Canonical enums for IncidentPilot (formerly AegisSRE) — Phase 2A.

These are shared by every telemetry adapter, the Incident Context Builder,
and (eventually) the future agentic layer. Keep them append-only where
possible — removing or renaming a value is a breaking change for anything
already persisted in Postgres.
"""

from enum import Enum


class SignalType(str, Enum):
    """What kind of telemetry an Observation represents (spec section 11)."""

    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    KUBERNETES_EVENT = "kubernetes_event"
    ALERT = "alert"
    SECURITY_EVENT = "security_event"
    DEPLOYMENT_EVENT = "deployment_event"


class Severity(str, Enum):
    """Shared severity scale for Observations and Incidents."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    UNKNOWN = "unknown"


class ObservationSource(str, Enum):
    """Where an Observation originated (spec section 9/12-17/25)."""

    PROMETHEUS = "prometheus"
    LOKI = "loki"
    TEMPO = "tempo"
    KUBERNETES = "kubernetes"
    ALERTMANAGER = "alertmanager"
    GIT = "git"
    TRIVY = "trivy"
    GITLEAKS = "gitleaks"
    MANUAL = "manual"


class IncidentStatus(str, Enum):
    """
    High-level lifecycle status of an Incident record.

    NOTE: the spec's Incident field list (section 18) includes both
    `status` and `current_phase` without fully separating their meaning.
    This implementation treats them as two different axes:
      - `status`        -> is the incident open, resolved, or closed
      - `current_phase` -> where the incident is in the Phase 2A pipeline
    See IncidentPhase below. Flag if you intended a single combined field.
    """

    OPEN = "open"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentPhase(str, Enum):
    """
    Phase 2A state machine (spec section 18). Deliberately does NOT
    include any RCA/investigation states — those belong to Phase 2B.
    """

    DETECTED = "detected"
    TRIAGED = "triaged"
    COLLECTING_CONTEXT = "collecting_context"
    READY_FOR_INVESTIGATION = "ready_for_investigation"


class EvidenceType(str, Enum):
    """Evidence category (spec section 21), mirrors SignalType but
    collapses kubernetes/deployment/security sources into their own
    evidence buckets since evidence is meant to be human/agent-readable."""

    METRIC = "metric"
    LOG = "log"
    TRACE = "trace"
    KUBERNETES_EVENT = "kubernetes_event"
    DEPLOYMENT = "deployment"
    SECURITY = "security"
    ALERT = "alert"
