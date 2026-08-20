from app.models.security_reports import GitleaksFinding
from app.normalizers.gitleaks_normalizer import normalize_gitleaks_findings
from shared.models import ObservationSource, Severity, SignalType

# Dummy/test-only value — never a real credential — per spec's "use only
# dummy/test secrets in any demo data". Included here only to prove the
# normalizer/model never carries a "Secret" field through at all.
DUMMY_SECRET_VALUE = "AKIA_TEST_DUMMY_NOT_REAL_00000000"


def make_finding(**overrides) -> GitleaksFinding:
    defaults = dict(
        Description="AWS Access Key",
        File="services/order-service/config.js",
        StartLine=12,
        Commit="abc1234def",
        Author="test-author",
        Date="2026-08-20T09:00:00Z",
        RuleID="aws-access-token",
        Fingerprint="abc1234def:services/order-service/config.js:aws-access-token:12",
    )
    defaults.update(overrides)
    return GitleaksFinding(**defaults)


def test_gitleaks_finding_never_carries_the_secret_value():
    # extra="ignore" on GitleaksFinding means Secret/Match are dropped at
    # parse time — assert that directly, not just "the normalizer doesn't
    # read it" (which wouldn't catch a future accidental read).
    finding = GitleaksFinding(
        Description="AWS Access Key",
        File="services/order-service/config.js",
        Secret=DUMMY_SECRET_VALUE,
        Match=DUMMY_SECRET_VALUE,
    )
    assert not hasattr(finding, "Secret")
    assert not hasattr(finding, "Match")
    assert DUMMY_SECRET_VALUE not in finding.model_dump_json()


def test_normalize_gitleaks_findings_basic_fields():
    observations = normalize_gitleaks_findings([make_finding()], cluster="cloudmart-k3s")

    assert len(observations) == 1
    obs = observations[0]
    assert obs.source == ObservationSource.GITLEAKS
    assert obs.signal_type == SignalType.SECURITY_EVENT
    assert obs.severity == Severity.CRITICAL
    assert obs.signal == "aws-access-token"
    assert obs.service == "order-service"
    assert obs.resource == "services/order-service/config.js"
    assert obs.metadata["commit"] == "abc1234def"


def test_normalize_gitleaks_findings_never_includes_secret_in_output():
    observations = normalize_gitleaks_findings([make_finding()], cluster="c")
    dumped = observations[0].model_dump_json()
    assert DUMMY_SECRET_VALUE not in dumped
    assert "Secret" not in dumped
    assert "Match" not in dumped


def test_derive_service_returns_none_for_non_services_path():
    observations = normalize_gitleaks_findings(
        [make_finding(File="docker-compose.yml")], cluster="c"
    )
    assert observations[0].service is None


def test_missing_rule_id_falls_back_to_secret_detected():
    observations = normalize_gitleaks_findings([make_finding(RuleID=None)], cluster="c")
    assert observations[0].signal == "secret_detected"


def test_malformed_date_falls_back_to_now():
    observations = normalize_gitleaks_findings([make_finding(Date="not-a-date")], cluster="c")
    assert observations[0].timestamp.tzinfo is not None


def test_empty_findings_list_returns_empty_observations():
    assert normalize_gitleaks_findings([], cluster="c") == []
