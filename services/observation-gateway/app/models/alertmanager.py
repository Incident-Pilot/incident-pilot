"""
Alertmanager webhook payload models — spec section 5.

Shape matches Alertmanager's documented webhook receiver format
(https://prometheus.io/docs/alerting/latest/configuration/#webhook_config),
version "4". This is the payload Alertmanager itself POSTs — not something
this service controls — so validation here exists to reject a malformed
or unrecognized body with a 4xx rather than letting a bad payload crash
normalization deeper in the pipeline.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str  # "firing" | "resolved"
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    startsAt: str
    endsAt: Optional[str] = None
    generatorURL: Optional[str] = None
    fingerprint: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status_must_be_known(cls, v: str) -> str:
        if v not in ("firing", "resolved"):
            raise ValueError(f"alert status must be 'firing' or 'resolved', got {v!r}")
        return v


class AlertmanagerWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: Optional[str] = None
    groupKey: Optional[str] = None
    truncatedAlerts: Optional[int] = None
    status: str  # "firing" | "resolved" — overall group status
    receiver: Optional[str] = None
    groupLabels: Dict[str, str] = Field(default_factory=dict)
    commonLabels: Dict[str, str] = Field(default_factory=dict)
    commonAnnotations: Dict[str, str] = Field(default_factory=dict)
    externalURL: Optional[str] = None
    alerts: List[AlertmanagerAlert]

    @field_validator("alerts")
    @classmethod
    def _alerts_must_not_be_empty(cls, v: List[AlertmanagerAlert]) -> List[AlertmanagerAlert]:
        if not v:
            raise ValueError("alerts must contain at least one alert")
        return v
