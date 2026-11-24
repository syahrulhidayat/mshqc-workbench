CREATE TABLE IF NOT EXISTS compute_jobs (
    id UUID PRIMARY KEY,
    input_hash CHAR(64) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'cancelled')
    ),
    method VARCHAR(16) NOT NULL,
    basis VARCHAR(32) NOT NULL,
    charge SMALLINT NOT NULL DEFAULT 0,
    multiplicity SMALLINT NOT NULL DEFAULT 1,
    geometry_xyz TEXT NOT NULL,
    engine VARCHAR(32),
    result_json JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE compute_jobs DROP CONSTRAINT IF EXISTS compute_jobs_status_check;
ALTER TABLE compute_jobs
    ADD CONSTRAINT compute_jobs_status_check
    CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled'));

CREATE INDEX IF NOT EXISTS idx_compute_jobs_created_at ON compute_jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_compute_jobs_status ON compute_jobs (status);
