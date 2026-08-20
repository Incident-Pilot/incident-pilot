import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

# Dummy/test-only — never a real credential — per spec's "use only
# dummy/test secrets in any demo data" instruction.
DUMMY_SECRET_VALUE = "AKIA_TEST_DUMMY_NOT_REAL_00000000"


@pytest.fixture()
def client(bypass_auth):
    app = create_app()
    bypass_auth(app)
    return TestClient(app)


def test_ingest_gitleaks_valid_report(client):
    resp = client.post(
        "/ingest/gitleaks",
        json=[
            {
                "Description": "AWS Access Key",
                "File": "services/order-service/config.js",
                "StartLine": 12,
                "Commit": "abc1234def",
                "Author": "test-author",
                "Date": "2026-08-20T09:00:00Z",
                "RuleID": "aws-access-token",
                "Fingerprint": "abc1234def:services/order-service/config.js:aws-access-token:12",
                # dummy value included deliberately, to prove it never
                # comes back out anywhere in the response
                "Secret": DUMMY_SECRET_VALUE,
                "Match": DUMMY_SECRET_VALUE,
            }
        ],
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["findings_ingested"] == 1
    assert len(body["observations_created"]) == 1
    assert DUMMY_SECRET_VALUE not in resp.text


def test_ingest_gitleaks_empty_report_is_accepted(client):
    resp = client.post("/ingest/gitleaks", json=[])
    assert resp.status_code == 202
    assert resp.json()["findings_ingested"] == 0


def test_ingest_gitleaks_malformed_body_returns_422(client):
    resp = client.post("/ingest/gitleaks", json={"not": "an array"})
    assert resp.status_code == 422


def test_ingest_gitleaks_observations_persisted_and_secret_free(client):
    client.post(
        "/ingest/gitleaks",
        json=[
            {
                "File": "services/order-service/config.js",
                "RuleID": "aws-access-token",
                "Secret": DUMMY_SECRET_VALUE,
            }
        ],
    )
    store = client.app.state.observation_store
    observations = asyncio.run(store.list_all())
    assert len(observations) == 1
    assert observations[0].signal == "aws-access-token"
    assert DUMMY_SECRET_VALUE not in observations[0].model_dump_json()


def test_ingest_trivy_valid_report(client):
    resp = client.post(
        "/ingest/trivy",
        json={
            "ArtifactName": "localhost:5000/cloudmart/order-service:v1",
            "Results": [
                {
                    "Target": "order-service (debian 11.6)",
                    "Vulnerabilities": [
                        {
                            "VulnerabilityID": "CVE-2023-1111",
                            "PkgName": "openssl",
                            "Severity": "CRITICAL",
                        }
                    ],
                }
            ],
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["findings_ingested"] == 1


def test_ingest_trivy_service_derived_from_artifact_name(client):
    client.post(
        "/ingest/trivy",
        json={
            "ArtifactName": "localhost:5000/cloudmart/product-service:v1",
            "Results": [{"Vulnerabilities": [{"VulnerabilityID": "CVE-1", "Severity": "HIGH"}]}],
        },
    )
    store = client.app.state.observation_store
    observations = asyncio.run(store.list_all())
    assert observations[0].service == "product-service"


def test_ingest_trivy_service_query_param_overrides(client):
    client.post(
        "/ingest/trivy?service=override-service",
        json={
            "ArtifactName": "localhost:5000/cloudmart/product-service:v1",
            "Results": [{"Vulnerabilities": [{"VulnerabilityID": "CVE-1", "Severity": "HIGH"}]}],
        },
    )
    store = client.app.state.observation_store
    observations = asyncio.run(store.list_all())
    assert observations[0].service == "override-service"


def test_ingest_trivy_empty_results_is_accepted(client):
    resp = client.post("/ingest/trivy", json={"ArtifactName": "x", "Results": []})
    assert resp.status_code == 202
    assert resp.json()["findings_ingested"] == 0


def test_ingest_trivy_malformed_body_returns_422(client):
    resp = client.post("/ingest/trivy", json=["not", "an", "object"])
    assert resp.status_code == 422
