"""
Gitleaks finding -> canonical Observation — spec section 5.

Only metadata ABOUT a finding (file, rule, commit, author, line) is ever
persisted — the actual secret value never reaches this module at all,
because `GitleaksFinding` (app/models/security_reports.py) drops it at
the API boundary. Severity is always CRITICAL: an actual credential
committed to git history is unambiguously critical regardless of which
one it is — this is a fixed fact about the finding type, not a judgment
call about impact.
"""

from datetime import datetime, timezone
from typing import List, Optional

from app.models.security_reports import GitleaksFinding
from shared.models import Observation, ObservationSource, Severity, SignalType


def _derive_service(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
        return None
    parts = file_path.split("/")
    if len(parts) >= 2 and parts[0] == "services":
        return parts[1]
    return None


def _parse_date(raw: Optional[str]) -> datetime:
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def normalize_gitleaks_findings(
    findings: List[GitleaksFinding], *, cluster: str
) -> List[Observation]:
    observations: List[Observation] = []
    for finding in findings:
        observations.append(
            Observation.new(
                source=ObservationSource.GITLEAKS,
                signal_type=SignalType.SECURITY_EVENT,
                severity=Severity.CRITICAL,
                cluster=cluster,
                service=_derive_service(finding.File),
                resource=finding.File,
                signal=finding.RuleID or "secret_detected",
                labels={},
                metadata={
                    "description": finding.Description,
                    "commit": finding.Commit,
                    "author": finding.Author,
                    "fingerprint": finding.Fingerprint,
                    "start_line": finding.StartLine,
                },
                timestamp=_parse_date(finding.Date),
            )
        )
    return observations
