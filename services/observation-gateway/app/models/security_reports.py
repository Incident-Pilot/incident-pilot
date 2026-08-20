"""
Gitleaks / Trivy report payload models — spec section 5.

Security-critical detail: Gitleaks' real JSON output includes the actual
leaked secret value in `Secret`/`Match` fields. `GitleaksFinding` uses
`extra="ignore"` (not `"allow"`, unlike every other payload model in this
service) specifically so those fields are dropped the instant Pydantic
parses the request body — they never become a Python attribute on the
model, so no code downstream (normalizer, Observation, database) can leak
them even by accident. This is stronger than "just don't read that field";
the field doesn't exist to read.
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class GitleaksFinding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    Description: Optional[str] = None
    File: Optional[str] = None
    StartLine: Optional[int] = None
    Commit: Optional[str] = None
    Author: Optional[str] = None
    Date: Optional[str] = None
    RuleID: Optional[str] = None
    Fingerprint: Optional[str] = None


GitleaksReport = List[GitleaksFinding]


class TrivyVulnerability(BaseModel):
    model_config = ConfigDict(extra="allow")

    VulnerabilityID: Optional[str] = None
    PkgName: Optional[str] = None
    InstalledVersion: Optional[str] = None
    FixedVersion: Optional[str] = None
    Severity: Optional[str] = None
    Title: Optional[str] = None
    PrimaryURL: Optional[str] = None


class TrivyResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    Target: Optional[str] = None
    Vulnerabilities: List[TrivyVulnerability] = Field(default_factory=list)


class TrivyReport(BaseModel):
    model_config = ConfigDict(extra="allow")

    ArtifactName: Optional[str] = None
    Results: List[TrivyResult] = Field(default_factory=list)
