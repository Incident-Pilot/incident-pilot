"""
Canonical Incident model — spec section 18.

Phase 2A only implements the DETECTED -> ... -> READY_FOR_INVESTIGATION
state machine. Do not add RCA/verification/remediation states here —
those belong to Phase 2B/2C models.
"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import IncidentPhase, IncidentStatus, Severity


class Incident(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    title: str

    severity: Severity
    status: IncidentStatus = IncidentStatus.OPEN
    current_phase: IncidentPhase = IncidentPhase.DETECTED

    created_at: datetime
    updated_at: datetime

    source: str  # e.g. "alertmanager" — what triggered incident creation

    affected_services: List[str] = Field(default_factory=list)
    affected_namespace: Optional[str] = None

    initial_alerts: List[str] = Field(default_factory=list)

    # Deliberately left unpopulated by Phase 2A. The Incident Context
    # Builder gathers evidence; it does not decide root cause. These
    # fields exist so Phase 2B agents have somewhere to write conclusions.
    root_cause: Optional[str] = None
    root_cause_confidence: Optional[float] = None

    @field_validator("incident_id", "title", "source")
    @classmethod
    def _must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty/blank")
        return v

    @field_validator("created_at", "updated_at")
    @classmethod
    def _must_be_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware (use UTC)")
        return v

    @field_validator("root_cause_confidence")
    @classmethod
    def _confidence_in_range(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 1.0):
            raise ValueError("root_cause_confidence must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _updated_not_before_created(self) -> "Incident":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        return self
