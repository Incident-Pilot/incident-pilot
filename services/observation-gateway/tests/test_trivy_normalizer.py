from app.models.security_reports import TrivyReport
from app.normalizers.trivy_normalizer import normalize_trivy_report
from shared.models import ObservationSource, Severity, SignalType


def make_report(**overrides) -> TrivyReport:
    defaults = dict(
        ArtifactName="localhost:5000/cloudmart/order-service:v1",
        Results=[
            {
                "Target": "order-service (debian 11.6)",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2023-1111",
                        "PkgName": "openssl",
                        "InstalledVersion": "1.1.1n",
                        "FixedVersion": "1.1.1o",
                        "Severity": "CRITICAL",
                        "Title": "openssl vuln",
                        "PrimaryURL": "https://example.test/cve-2023-1111",
                    },
                    {
                        "VulnerabilityID": "CVE-2023-2222",
                        "PkgName": "curl",
                        "Severity": "LOW",
                    },
                ],
            }
        ],
    )
    defaults.update(overrides)
    return TrivyReport(**defaults)


def test_normalize_trivy_report_basic_fields():
    observations = normalize_trivy_report(make_report(), cluster="cloudmart-k3s")

    assert len(observations) == 2
    critical = next(o for o in observations if o.signal == "CVE-2023-1111")
    assert critical.source == ObservationSource.TRIVY
    assert critical.signal_type == SignalType.SECURITY_EVENT
    assert critical.severity == Severity.CRITICAL
    assert critical.service == "order-service"
    assert critical.resource == "openssl"
    assert critical.metadata["fixed_version"] == "1.1.1o"


def test_severity_mapping():
    report = make_report(
        Results=[
            {
                "Vulnerabilities": [
                    {"VulnerabilityID": "A", "Severity": "CRITICAL"},
                    {"VulnerabilityID": "B", "Severity": "HIGH"},
                    {"VulnerabilityID": "C", "Severity": "MEDIUM"},
                    {"VulnerabilityID": "D", "Severity": "LOW"},
                    {"VulnerabilityID": "E", "Severity": "UNKNOWN"},
                    {"VulnerabilityID": "F", "Severity": "bogus"},
                ]
            }
        ]
    )
    observations = normalize_trivy_report(report, cluster="c")
    by_id = {o.signal: o.severity for o in observations}
    assert by_id["A"] == Severity.CRITICAL
    assert by_id["B"] == Severity.WARNING
    assert by_id["C"] == Severity.WARNING
    assert by_id["D"] == Severity.INFO
    assert by_id["E"] == Severity.UNKNOWN
    assert by_id["F"] == Severity.UNKNOWN


def test_service_derived_from_artifact_name_with_registry_port():
    observations = normalize_trivy_report(
        make_report(ArtifactName="localhost:5000/cloudmart/product-service:v1"), cluster="c"
    )
    assert all(o.service == "product-service" for o in observations)


def test_service_override_takes_precedence():
    observations = normalize_trivy_report(
        make_report(), cluster="c", service_override="override-service"
    )
    assert all(o.service == "override-service" for o in observations)


def test_no_vulnerabilities_returns_empty_list():
    report = make_report(Results=[{"Target": "clean-target", "Vulnerabilities": []}])
    assert normalize_trivy_report(report, cluster="c") == []


def test_missing_vulnerabilities_key_does_not_raise():
    report = make_report(Results=[{"Target": "clean-target"}])
    assert normalize_trivy_report(report, cluster="c") == []


def test_findings_capped_and_most_severe_kept_first():
    vulns = [{"VulnerabilityID": f"LOW-{i}", "Severity": "LOW"} for i in range(250)]
    vulns.append({"VulnerabilityID": "THE-CRITICAL-ONE", "Severity": "CRITICAL"})
    report = make_report(Results=[{"Vulnerabilities": vulns}])

    observations = normalize_trivy_report(report, cluster="c")
    assert len(observations) == 200
    assert any(o.signal == "THE-CRITICAL-ONE" for o in observations)
