-- Versioned migration infrastructure. Bootstrapped in the same transaction as
-- pending migrations; no application/source objects are changed by this file.
CREATE TABLE IF NOT EXISTS public.app_schema_migrations (
    version TEXT PRIMARY KEY CHECK (version ~ '^[0-9]{4}$'),
    filename TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
