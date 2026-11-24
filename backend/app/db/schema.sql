CREATE TABLE IF NOT EXISTS compute_jobs (
    id UUID PRIMARY KEY,
    input_hash CHAR(64) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL,
    method VARCHAR(24) NOT NULL,
    basis VARCHAR(64) NOT NULL,
    charge SMALLINT NOT NULL DEFAULT 0,
    multiplicity SMALLINT NOT NULL DEFAULT 1,
    eri_method VARCHAR(16) NOT NULL DEFAULT 'auto',
    freeze_core BOOLEAN NOT NULL DEFAULT FALSE,
    geometry_xyz TEXT NOT NULL,
    engine VARCHAR(64),
    result_json JSONB,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE compute_jobs ADD COLUMN IF NOT EXISTS eri_method VARCHAR(16) NOT NULL DEFAULT 'auto';
ALTER TABLE compute_jobs ADD COLUMN IF NOT EXISTS freeze_core BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE compute_jobs ALTER COLUMN method TYPE VARCHAR(24);
ALTER TABLE compute_jobs ALTER COLUMN basis TYPE VARCHAR(64);
ALTER TABLE compute_jobs ALTER COLUMN engine TYPE VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_compute_jobs_created_at ON compute_jobs (created_at DESC);
