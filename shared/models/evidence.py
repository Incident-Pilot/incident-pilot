"""
Canonical Evidence model — spec sections 21-22.

Evidence is what the future RCA agent is allowed to cite. It must always
be traceable back to a concrete telemetry query/reference so a conclusion
can be independently re-verified. Never let an agent invent an evidence_id
that doesn't exist in this table — that traceability is the whole point.
"""

from typing import Any, Dict, Optional
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import EvidenceType, ObservationSource


class RawReference(BaseModel):
    """The provenance payload — enough detail to re-run the exact query
    that produced this evidence (spec section 22)."""

    model_config = ConfigDict(extra="forbid")

    query: Optional[str] = None
    trace_id: Optional[str] = None
    log_query: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    incident_id: str

    type: EvidenceType
    source: ObservationSource
    timestamp: datetime

    service: Optional[str] = None
    resource: Optional[str] = None

    summary: str

    # Link back to the Observation this evidence was derived from, when
    # applicable (not all evidence — e.g. hand-added notes — has one).
    observation_id: Optional[str] = None

    raw_reference: RawReference = Field(default_factory=RawReference)

    @field_validator("evidence_id", "incident_id", "summary")
    @classmethod
    def _must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty/blank")
        return v

    @field_validator("timestamp")
    @classmethod
    def _must_be_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (use UTC)")
        return v
