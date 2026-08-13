"""
Canonical Observation model — spec section 11.

Every telemetry adapter (Prometheus, Loki, Tempo, Kubernetes, Alertmanager,
Trivy, Gitleaks, deployment metadata) must eventually normalize into this
shape. This model intentionally carries NO reasoning fields — no root
cause, no suggested action. It is a normalized fact, nothing more.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import ObservationSource, Severity, SignalType


class Correlation(BaseModel):
    """Cross-references that let this Observation be linked to other
    evidence without duplicating data (spec section 11 `correlation`)."""

    model_config = ConfigDict(extra="forbid")

    trace_id: Optional[str] = None
    deployment_id: Optional[str] = None
    incident_id: Optional[str] = None


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    timestamp: datetime

    source: ObservationSource
    signal_type: SignalType
    severity: Severity = Severity.UNKNOWN

    cluster: str
    namespace: Optional[str] = None
    service: Optional[str] = None
    resource: Optional[str] = None

    signal: str
    value: Optional[float] = None

    labels: Dict[str, str] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    correlation: Correlation = Field(default_factory=Correlation)

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError(
                "Observation.timestamp must be timezone-aware (use UTC). "
                "Naive datetimes are a common source of incident-timeline bugs."
            )
        return v

    @field_validator("observation_id", "signal", "cluster")
    @classmethod
    def _must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty/blank")
        return v

    @classmethod
    def new(
        cls,
        *,
        source: ObservationSource,
        signal_type: SignalType,
        cluster: str,
        signal: str,
        severity: Severity = Severity.UNKNOWN,
        namespace: Optional[str] = None,
        service: Optional[str] = None,
        resource: Optional[str] = None,
        value: Optional[float] = None,
        labels: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        correlation: Optional[Correlation] = None,
        timestamp: Optional[datetime] = None,
    ) -> "Observation":
        """Convenience constructor for adapters: generates a prefixed
        observation_id and defaults timestamp to now (UTC)."""

        return cls(
            observation_id=f"obs-{source.value}-{uuid.uuid4().hex[:12]}",
            timestamp=timestamp or datetime.now(timezone.utc),
            source=source,
            signal_type=signal_type,
            severity=severity,
            cluster=cluster,
            namespace=namespace,
            service=service,
            resource=resource,
            signal=signal,
            value=value,
            labels=labels or {},
            metadata=metadata or {},
            correlation=correlation or Correlation(),
        )
