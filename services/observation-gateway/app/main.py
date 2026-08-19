"""
Observation Gateway FastAPI app — spec section 12.

Step 7 wires up only what the Alertmanager webhook needs: the app itself,
/health, /ready, and the webhook route. The rest of the API surface
(/incidents, /topology, etc.) is added in step 14 once persistence (step 8),
correlation (step 9), the context builder (step 10), and topology (step 11)
exist to back it.
"""

from fastapi import FastAPI

from app.api.webhooks import router as webhooks_router
from app.storage.memory import InMemoryIncidentStore, InMemoryObservationStore


def create_app() -> FastAPI:
    app = FastAPI(title="IncidentPilot Observation Gateway")

    # In-memory for now (step 7) — step 8 replaces these two lines with
    # Postgres-backed stores behind the same ObservationStore/IncidentStore
    # interfaces, so nothing above this changes.
    app.state.observation_store = InMemoryObservationStore()
    app.state.incident_store = InMemoryIncidentStore()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        return {"status": "ready"}

    app.include_router(webhooks_router)

    return app


app = create_app()
