"""
LogEntry (app/collectors/loki_adapter.py's parsed shape) -> canonical
Observation — spec section 9/13.

One Observation per line, capped at `max_entries` — spec section 5's "do
not dump unbounded raw log volume into downstream storage" applies here,
not just to the adapter layer. Severity is deliberately left UNKNOWN: this
module normalizes structure, it doesn't classify free-text messages as
"this looks bad" — that's reasoning, out of scope for this service.
"""

from typing import List

from app.collectors.loki_adapter import LogEntry
from shared.models import Observation, ObservationSource, SignalType


def normalize_log_entries(
    entries: List[LogEntry], *, cluster: str, max_entries: int = 50
) -> List[Observation]:
    observations: List[Observation] = []
    for entry in entries[:max_entries]:
        observations.append(
            Observation.new(
                source=ObservationSource.LOKI,
                signal_type=SignalType.LOG,
                cluster=cluster,
                signal="log_line",
                namespace=entry.namespace,
                service=entry.service,
                resource=entry.pod,
                labels=entry.labels,
                metadata={"message": entry.message, "container": entry.container},
                timestamp=entry.timestamp,
            )
        )
    return observations
