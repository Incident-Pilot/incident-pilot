# IncidentPilot

Agentic AI Incident Response and SRE platform for the CloudMart k3s
environment. Currently in **Phase 2A**: Observation Gateway + Incident
Ingestion + Incident Context Builder (deterministic, no LLM reasoning
yet — see `docs/PROGRESS.md` for the full plan and current status).

## Repo layout

```
incidentpilot/
├── services/observation-gateway/   # FastAPI service (Phase 2A core)
├── shared/models/                  # Canonical Observation/Incident/Evidence schemas
├── infrastructure/kubernetes/      # k3s manifests
└── docs/                           # PROGRESS.md tracks task-by-task status
```

## Quickstart (current state: shared models only)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate 
pip install -r requirements-dev.txt
python -m pytest shared/tests -v
```

Expected: 13 tests pass.
