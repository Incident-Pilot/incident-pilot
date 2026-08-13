"""
Common result type for every telemetry adapter (Prometheus, Loki, Tempo,
Kubernetes). Spec section 29: the gateway must not fail permanently if one
backend is temporarily unavailable — every adapter call returns a status
instead of raising, so the Incident Context Builder can assemble partial
context and record which sources were reachable.
"""

from enum import Enum
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class SourceStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    PARTIAL = "partial"


class AdapterResult(BaseModel, Generic[T]):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    status: SourceStatus
    data: Optional[T] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == SourceStatus.AVAILABLE
