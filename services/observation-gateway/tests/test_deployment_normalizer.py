from datetime import datetime, timezone

from app.normalizers.deployment_normalizer import normalize_deployment
from shared.models import Deployment, ObservationSource, SignalType


def make_deployment(**overrides) -> Deployment:
    defaults = dict(
        deployment_id="dep-order-service-abc1234",
        service="order-service",
        namespace="cloudmart-prod",
        commit_sha="abc1234",
        branch="main",
        image_tag="localhost:5000/cloudmart/order-service:v1",
        rollout_revision="7",
        deployed_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        success=True,
    )
    defaults.update(overrides)
    return Deployment(**defaults)


def test_normalize_deployment_basic_fields():
    obs = normalize_deployment(make_deployment(), cluster="cloudmart-k3s")

    assert obs.source == ObservationSource.GIT
    assert obs.signal_type == SignalType.DEPLOYMENT_EVENT
    assert obs.service == "order-service"
    assert obs.namespace == "cloudmart-prod"
    assert obs.timestamp == datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    assert obs.metadata["commit_sha"] == "abc1234"
    assert obs.metadata["branch"] == "main"
    assert obs.metadata["rollout_revision"] == "7"
    assert obs.metadata["success"] is True


def test_normalize_deployment_links_incident_id():
    obs = normalize_deployment(make_deployment(), cluster="c", incident_id="INC-0001")
    assert obs.correlation.incident_id == "INC-0001"


def test_normalize_deployment_incident_id_optional():
    obs = normalize_deployment(make_deployment(), cluster="c")
    assert obs.correlation.incident_id is None
