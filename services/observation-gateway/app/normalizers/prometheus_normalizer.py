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

# Relative change (vs. baseline) beyond which a metric is called "rising"
# or "falling" rather than "stable" — see summarize_metric_series().
_TREND_REL_THRESHOLD = 0.05


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
    one parseable sample; series that don't parse are skipped, not raised.

    Deprecated in favor of `summarize_metric_series` for the Context
    Builder's own probes (see that function's docstring) — kept for other
    callers that still want one Observation per series."""

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


def _classify_trend(baseline: float, current: float) -> str:
    diff = current - baseline
    if abs(diff) < 1e-9:
        return "stable"
    if baseline == 0:
        return "rising" if diff > 0 else "falling"
    pct_change = diff / abs(baseline)
    if pct_change > _TREND_REL_THRESHOLD:
        return "rising"
    if pct_change < -_TREND_REL_THRESHOLD:
        return "falling"
    return "stable"


def summarize_metric_series(
    data: Dict[str, Any],
    *,
    signal: str,
    cluster: str,
    namespace: Optional[str] = None,
    service: Optional[str] = None,
    unit: str = "",
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
) -> Optional[Observation]:
    """Collapses a Prometheus `query_range` matrix into a SINGLE summary
    Observation per metric per service, instead of one Observation per
    series/sample. A probe like `pod_restarts` can return one series per
    matching pod (`pod=~"order-service.*"` matches every replica, current
    and long-terminated), each with many samples across the lookback
    window — turned 1:1 into Evidence that was ~230 near-duplicate rows
    for a single real incident (see docs/LIVE_CLUSTER_VERIFICATION.md).
    This mirrors spec section 13's log-volume principle applied to metrics.

    `baseline` is the sum, across all matching series, of each series'
    first sample; `current` is the same for each series' last sample;
    `trend` ("rising"/"falling"/"stable") is derived from the change
    between them. Returns None when no series has a parseable sample."""

    baseline_total = 0.0
    current_total = 0.0
    latest_ts: Optional[float] = None
    series_count = 0
    combined_labels: Dict[str, str] = {}

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

        series_count += 1
        combined_labels = combined_labels or metric_labels
        baseline_total += samples[0][1]
        current_total += samples[-1][1]
        series_latest_ts = samples[-1][0]
        if latest_ts is None or series_latest_ts > latest_ts:
            latest_ts = series_latest_ts

    if series_count == 0 or latest_ts is None:
        return None

    return Observation.new(
        source=ObservationSource.PROMETHEUS,
        signal_type=SignalType.METRIC,
        cluster=cluster,
        signal=signal,
        namespace=namespace or combined_labels.get("namespace"),
        service=service or combined_labels.get("service") or combined_labels.get("app"),
        value=current_total,
        labels=combined_labels,
        metadata={
            "baseline": baseline_total,
            "current": current_total,
            "trend": _classify_trend(baseline_total, current_total),
            "unit": unit,
            "series_count": series_count,
            "window_start": window_start.isoformat() if window_start else None,
            "window_end": window_end.isoformat() if window_end else None,
        },
        timestamp=datetime.fromtimestamp(latest_ts, tz=timezone.utc),
    )
