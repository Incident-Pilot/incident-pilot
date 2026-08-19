from app.normalizers.prometheus_normalizer import normalize_metric_series


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
