"""
Trivy vulnerability -> canonical Observation — spec section 5.

Structural only: Trivy already assigns severity (CRITICAL/HIGH/MEDIUM/
LOW/UNKNOWN); this maps that label onto the canonical Severity enum, same
passthrough discipline as the Alertmanager/K8s-event normalizers — no
independent judgment about which CVEs actually matter for this service.

A real image scan can return dozens to hundreds of vulnerabilities;
capped at `_MAX_FINDINGS`, keeping the most severe first — a volume
guard (spec section 5's "don't dump unbounded raw volume" applies here
same as it does to Loki log lines), not a claim that lower-severity CVEs
don't matter.
"""

from datetime import datetime, timezone
from typing import List, Optional, Tuple

from app.models.security_reports import TrivyReport, TrivyVulnerability
from shared.models import Observation, ObservationSource, Severity, SignalType

_SEVERITY_MAP = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH": Severity.WARNING,
    "MEDIUM": Severity.WARNING,
    "LOW": Severity.INFO,
    "UNKNOWN": Severity.UNKNOWN,
}
_SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 0}
_MAX_FINDINGS = 200


def _derive_service(artifact_name: Optional[str]) -> Optional[str]:
    """`localhost:5000/cloudmart/order-service:v1` -> `order-service`.
    Care needed since the registry host itself contains a colon."""
    if not artifact_name:
        return None
    last_segment = artifact_name.rsplit("/", 1)[-1]
    service = last_segment.split(":")[0]
    return service or None


def normalize_trivy_report(
    report: TrivyReport, *, cluster: str, service_override: Optional[str] = None
) -> List[Observation]:
    service = service_override or _derive_service(report.ArtifactName)
    now = datetime.now(timezone.utc)

    pairs: List[Tuple[Optional[str], TrivyVulnerability]] = []
    for result in report.Results:
        for vuln in result.Vulnerabilities:
            pairs.append((result.Target, vuln))

    pairs.sort(
        key=lambda pair: _SEVERITY_RANK.get((pair[1].Severity or "").upper(), 0),
        reverse=True,
    )

    observations: List[Observation] = []
    for target, vuln in pairs[:_MAX_FINDINGS]:
        observations.append(
            Observation.new(
                source=ObservationSource.TRIVY,
                signal_type=SignalType.SECURITY_EVENT,
                severity=_SEVERITY_MAP.get((vuln.Severity or "").upper(), Severity.UNKNOWN),
                cluster=cluster,
                service=service,
                resource=vuln.PkgName,
                signal=vuln.VulnerabilityID or "unknown_cve",
                labels={},
                metadata={
                    "installed_version": vuln.InstalledVersion,
                    "fixed_version": vuln.FixedVersion,
                    "title": vuln.Title,
                    "primary_url": vuln.PrimaryURL,
                    "target": target,
                },
                timestamp=now,
            )
        )
    return observations
