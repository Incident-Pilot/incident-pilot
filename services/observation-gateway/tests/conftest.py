"""Shared test fixtures.

`bypass_auth` exists because step 14 made every non-health route require
a bearer token (app/api/auth.py). Existing webhook/topology/ingestion
tests are about that endpoint's own logic, not about auth, so they
override the dependency rather than each carrying a real key + header.
Auth itself gets its own dedicated coverage in test_api_auth.py, which
does NOT use this fixture.
"""

import pytest
from fastapi import FastAPI

from app.api.auth import require_api_key


@pytest.fixture()
def bypass_auth():
    def _bypass(app: FastAPI) -> None:
        app.dependency_overrides[require_api_key] = lambda: None

    return _bypass
