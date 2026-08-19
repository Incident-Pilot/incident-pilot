"""
Prometheus range-query result -> canonical Observation — spec section 9/12.

Structural only: takes whatever series `query_range()` returned and turns
each one into an Observation (latest sample as `value`, a capped sample
history in `metadata`). Does not judge whether a value is "bad" — no
threshold logic, no severity inference from the number. Which PromQL to
run in the first place is the Context Builder's job
(app/context/incident_context_builder.py), not this module.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shared.models import Observation, ObservationSource, SignalType


def normalize_metric_series(
    data: Dict[str, Any],
    *,
    signal: str,
    cluster: str,
    namespace: Optional[str] = None,
    service: Optional[str] = None,
    max_samples_kept: int = 20,
) -> List[Observation]:
    """`data` is the `data` field of a Prometheus `query_range` response
    (`{"resultType": "matrix", "result": [{"metric": {...}, "values": [[ts,
    "val"], ...]}, ...]}`). One Observation per series that has at least
    one parseable sample; series that don't parse are skipped, not raised."""

    observations: List[Observation] = []
    for series in (data or {}).get("result", []) or []:
        metric_labels: Dict[str, str] = series.get("metric", {}) or {}
        samples = []
        for point in series.get("values", []) or []:
            try:
                ts_raw, val_raw = point
                samples.append((float(ts_raw), float(val_raw)))
            except (TypeError, ValueError):
                continue
        if not samples:
            continue

        latest_ts, latest_val = samples[-1]
        observations.append(
            Observation.new(
                source=ObservationSource.PROMETHEUS,
                signal_type=SignalType.METRIC,
                cluster=cluster,
                signal=signal,
                namespace=namespace or metric_labels.get("namespace"),
                service=service or metric_labels.get("service") or metric_labels.get("app"),
                resource=metric_labels.get("pod") or metric_labels.get("instance"),
                value=latest_val,
                labels=metric_labels,
                metadata={"samples": samples[-max_samples_kept:]},
                timestamp=datetime.fromtimestamp(latest_ts, tz=timezone.utc),
            )
        )
    return observations
