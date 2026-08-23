-- Observation Gateway schema — spec section 11.
--
-- All five tables from the spec are created here as one idempotent
-- migration (CREATE ... IF NOT EXISTS), since they're a fixed, already-
-- specced set and creating them together avoids repeated ad-hoc migrations
-- later. Only `incidents` and `observations` have a Python-side store
-- implementation as of step 8 — `evidence`, `deployments`, and
-- `service_topology` exist as tables now so later steps (10, 11, 12) don't
-- need a schema change, but nothing writes to them yet.

CREATE TABLE IF NOT EXISTS incidents (
    incident_id            TEXT PRIMARY KEY,
    title                  TEXT NOT NULL,
    severity               TEXT NOT NULL,
    status                 TEXT NOT NULL,
    current_phase          TEXT NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL,
    source                 TEXT NOT NULL,
    affected_services      JSONB NOT NULL DEFAULT '[]',
    affected_namespace     TEXT,
    initial_alerts         JSONB NOT NULL DEFAULT '[]',
    root_cause             TEXT,
    root_cause_confidence  DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id  TEXT PRIMARY KEY,
    "timestamp"     TIMESTAMPTZ NOT NULL,
    source          TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    severity        TEXT NOT NULL,
    cluster         TEXT NOT NULL,
    namespace       TEXT,
    service         TEXT,
    resource        TEXT,
    signal          TEXT NOT NULL,
    value           DOUBLE PRECISION,
    labels          JSONB NOT NULL DEFAULT '{}',
    metadata        JSONB NOT NULL DEFAULT '{}',
    trace_id        TEXT,
    deployment_id   TEXT,
    incident_id     TEXT REFERENCES incidents(incident_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_incident_id ON observations(incident_id);
CREATE INDEX IF NOT EXISTS idx_observations_timestamp ON observations("timestamp");

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id     TEXT PRIMARY KEY,
    incident_id     TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    type            TEXT NOT NULL,
    source          TEXT NOT NULL,
    "timestamp"     TIMESTAMPTZ NOT NULL,
    service         TEXT,
    resource        TEXT,
    summary         TEXT NOT NULL,
    observation_id  TEXT REFERENCES observations(observation_id) ON DELETE SET NULL,
    raw_reference   JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_evidence_incident_id ON evidence(incident_id);

CREATE TABLE IF NOT EXISTS deployments (
    deployment_id      TEXT PRIMARY KEY,
    service            TEXT NOT NULL,
    namespace          TEXT NOT NULL,
    commit_sha         TEXT,
    image_tag          TEXT,
    rollout_revision   TEXT,
    deployed_at        TIMESTAMPTZ NOT NULL,
    success            BOOLEAN,
    metadata           JSONB NOT NULL DEFAULT '{}'
);
-- Added step 12: spec section 5 lists "branch" as a required deployment
-- field alongside commit SHA, but the original migration (Task 7) missed
-- it. ADD COLUMN IF NOT EXISTS keeps this migration idempotent for
-- databases that already ran the CREATE TABLE above.
ALTER TABLE deployments ADD COLUMN IF NOT EXISTS branch TEXT;
CREATE INDEX IF NOT EXISTS idx_deployments_service ON deployments(service);

CREATE TABLE IF NOT EXISTS service_topology (
    service      TEXT PRIMARY KEY,
    namespace    TEXT NOT NULL,
    depends_on   JSONB NOT NULL DEFAULT '[]',
    updated_at   TIMESTAMPTZ NOT NULL
);

-- Added to fix a completeness gap (spec section 13/37): the Incident
-- Context Builder always computed per-source AVAILABLE/UNAVAILABLE/
-- TIMEOUT/PARTIAL status but nothing durable ever stored it, so a Loki/
-- Tempo/Kubernetes failure during collection was invisible outside of
-- reading source code. One row per (incident, source); a re-run of
-- context collection for the same incident replaces its rows rather than
-- appending, so this always reflects the latest collection attempt.
CREATE TABLE IF NOT EXISTS incident_source_status (
    incident_id        TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    source             TEXT NOT NULL,
    status             TEXT NOT NULL,
    error              TEXT,
    observation_count  INTEGER NOT NULL DEFAULT 0,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (incident_id, source)
);
