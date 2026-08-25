from app.normalizers.prometheus_normalizer import normalize_metric_series, summarize_metric_series


def test_normalize_metric_series_uses_latest_sample_as_value():
    data = {
        "result": [
            {
                "metric": {"pod": "order-service-abc123", "namespace": "cloudmart-prod"},
                "values": [[1000.0, "1"], [1030.0, "2"], [1060.0, "5"]],
            }
        ]
    }
    observations = normalize_metric_series(data, signal="pod_restarts", cluster="cloudmart-k3s")

    assert len(observations) == 1
    obs = observations[0]
    assert obs.value == 5.0
    assert obs.signal == "pod_restarts"
    assert obs.resource == "order-service-abc123"
    assert obs.namespace == "cloudmart-prod"


def test_normalize_metric_series_caps_kept_samples():
    values = [[1000.0 + i, str(i)] for i in range(50)]
    data = {"result": [{"metric": {}, "values": values}]}
    observations = normalize_metric_series(
        data, signal="x", cluster="c", max_samples_kept=5
    )
    assert len(observations[0].metadata["samples"]) == 5
    assert observations[0].metadata["samples"][-1] == (1049.0, 49.0)


def test_normalize_metric_series_skips_series_with_no_parseable_samples():
    data = {"result": [{"metric": {}, "values": [["bad", "bad"]]}]}
    observations = normalize_metric_series(data, signal="x", cluster="c")
    assert observations == []


def test_normalize_metric_series_empty_result_returns_empty_list():
    assert normalize_metric_series({}, signal="x", cluster="c") == []
    assert normalize_metric_series({"result": []}, signal="x", cluster="c") == []


def test_normalize_metric_series_service_label_fallback():
    data = {
        "result": [
            {"metric": {"app": "order-service"}, "values": [[1000.0, "1"]]},
        ]
    }
    observations = normalize_metric_series(data, signal="x", cluster="c")
    assert observations[0].service == "order-service"


def test_normalize_metric_series_explicit_namespace_service_override_labels():
    data = {"result": [{"metric": {"namespace": "wrong", "app": "wrong"}, "values": [[1000.0, "1"]]}]}
    observations = normalize_metric_series(
        data, signal="x", cluster="c", namespace="cloudmart-prod", service="order-service"
    )
    assert observations[0].namespace == "cloudmart-prod"
    assert observations[0].service == "order-service"


# --- summarize_metric_series ------------------------------------------------


def test_summarize_metric_series_collapses_multiple_series_into_one_observation():
    data = {
        "result": [
            {"metric": {"pod": "order-service-abc"}, "values": [[1000.0, "0"], [1900.0, "0"]]},
            {"metric": {"pod": "order-service-def"}, "values": [[1000.0, "0"], [1900.0, "0"]]},
            {"metric": {"pod": "order-service-ghi"}, "values": [[1000.0, "0"], [1900.0, "0"]]},
        ]
    }
    obs = summarize_metric_series(data, signal="pod_restarts", cluster="c", service="order-service")

    assert obs is not None
    assert obs.metadata["series_count"] == 3
    assert obs.metadata["baseline"] == 0.0
    assert obs.metadata["current"] == 0.0
    assert obs.metadata["trend"] == "stable"
    assert obs.value == 0.0


def test_summarize_metric_series_sums_across_series_for_baseline_and_current():
    data = {
        "result": [
            {"metric": {}, "values": [[1000.0, "1"], [1900.0, "4"]]},
            {"metric": {}, "values": [[1000.0, "2"], [1900.0, "2"]]},
        ]
    }
    obs = summarize_metric_series(data, signal="cpu_usage_seconds", cluster="c", service="order-service")

    assert obs.metadata["baseline"] == 3.0
    assert obs.metadata["current"] == 6.0
    assert obs.value == 6.0


def test_summarize_metric_series_classifies_rising_and_falling_trend():
    rising = {"result": [{"metric": {}, "values": [[1000.0, "10"], [1900.0, "20"]]}]}
    falling = {"result": [{"metric": {}, "values": [[1000.0, "20"], [1900.0, "10"]]}]}

    assert summarize_metric_series(rising, signal="x", cluster="c").metadata["trend"] == "rising"
    assert summarize_metric_series(falling, signal="x", cluster="c").metadata["trend"] == "falling"


def test_summarize_metric_series_small_relative_change_is_stable():
    data = {"result": [{"metric": {}, "values": [[1000.0, "100"], [1900.0, "101"]]}]}
    obs = summarize_metric_series(data, signal="x", cluster="c")
    assert obs.metadata["trend"] == "stable"


def test_summarize_metric_series_carries_unit_and_window():
    from datetime import datetime, timezone

    window_start = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    window_end = datetime(2026, 8, 20, 9, 15, tzinfo=timezone.utc)
    data = {"result": [{"metric": {}, "values": [[1000.0, "1"], [1900.0, "1"]]}]}
    obs = summarize_metric_series(
        data, signal="x", cluster="c", unit="cores", window_start=window_start, window_end=window_end
    )
    assert obs.metadata["unit"] == "cores"
    assert obs.metadata["window_start"] == window_start.isoformat()
    assert obs.metadata["window_end"] == window_end.isoformat()


def test_summarize_metric_series_no_parseable_samples_returns_none():
    assert summarize_metric_series({}, signal="x", cluster="c") is None
    assert summarize_metric_series({"result": []}, signal="x", cluster="c") is None
    assert (
        summarize_metric_series({"result": [{"metric": {}, "values": [["bad", "bad"]]}]}, signal="x", cluster="c")
        is None
    )
