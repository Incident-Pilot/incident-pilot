"""
Span (app/collectors/tempo_adapter.py's parsed shape) -> canonical
Observation — spec section 9/14.

Only spans already flagged `status == "error"` by the adapter's own
error-tag/http-status detection become Observations — that's a
structural read of a field the span already carries, not root-cause
judgment. Answering "where did latency increase" would require comparing
against a baseline, which is analysis; this module doesn't attempt it.
"""

from typing import List, Optional

from app.collectors.tempo_adapter import Span
from shared.models import Correlation, Observation, ObservationSource, Severity, SignalType


def normalize_error_spans(
    spans: List[Span], *, cluster: str, namespace: Optional[str] = None
) -> List[Observation]:
    observations: List[Observation] = []
    for span in spans:
        if span.status != "error":
            continue
        observations.append(
            Observation.new(
                source=ObservationSource.TEMPO,
                signal_type=SignalType.TRACE,
                severity=Severity.WARNING,
                cluster=cluster,
                namespace=namespace,
                service=span.service,
                signal="trace_error",
                labels={},
                metadata={
                    "operation": span.operation,
                    "duration_ms": span.duration_ms,
                    "span_id": span.span_id,
                    "tags": span.tags,
                },
                correlation=Correlation(trace_id=span.trace_id),
                timestamp=span.start_time,
            )
        )
    return observations
