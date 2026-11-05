-- M1: source records, ingestion bookkeeping, and reserved canonical identity.
-- Executed transactionally by database.migrate; every relation is in public.

CREATE TABLE public.tigzig_fund_houses (
    fund_house_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    original_amc_name TEXT NOT NULL UNIQUE CHECK (btrim(original_amc_name) <> ''),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.tigzig_schemes (
    source TEXT NOT NULL CHECK (btrim(source) <> '' AND source = btrim(source)),
    scheme_code BIGINT NOT NULL CHECK (scheme_code >= 0),
    fund_house_id BIGINT NOT NULL REFERENCES public.tigzig_fund_houses(fund_house_id),
    scheme_name TEXT NOT NULL CHECK (btrim(scheme_name) <> ''),
    scheme_type TEXT,
    category TEXT,
    category_sub TEXT,
    category_group_clean TEXT,
    category_group TEXT,
    scheme_plan TEXT,
    scheme_option TEXT,
    first_nav_date DATE,
    last_nav_date DATE,
    is_active BOOLEAN,
    is_stale BOOLEAN,
    latest_nav NUMERIC(24,10) CHECK (latest_nav > 0 AND latest_nav <> 'NaN'::numeric),
    latest_nav_date DATE,
    latest_aaum_quarter TEXT,
    latest_aaum_quarter_end DATE,
    latest_aaum_cr NUMERIC(24,6) CHECK (latest_aaum_cr >= 0 AND latest_aaum_cr <> 'NaN'::numeric),
    latest_aaum_lakhs NUMERIC(26,4) CHECK (latest_aaum_lakhs >= 0 AND latest_aaum_lakhs <> 'NaN'::numeric),
    txic_code TEXT,
    temp_sif_code TEXT,
    temp_plan_basis TEXT,
    temp_idcw_mode TEXT,
    temp_idcw_freq TEXT,
    temp_n_nav_days INTEGER CHECK (temp_n_nav_days >= 0),
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(source_metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, scheme_code),
    CHECK (first_nav_date <= last_nav_date),
    CHECK ((latest_nav IS NULL) = (latest_nav_date IS NULL)),
    CHECK (latest_aaum_cr IS NULL OR latest_aaum_lakhs IS NULL OR
           latest_aaum_cr = round(latest_aaum_lakhs / 100, 6))
);

CREATE TABLE public.tigzig_scheme_isins (
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    identifier_type TEXT NOT NULL CHECK (identifier_type IN ('PRIMARY', 'SECONDARY')),
    isin TEXT NOT NULL CHECK (btrim(isin) <> '' AND isin = btrim(isin)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, scheme_code, identifier_type),
    UNIQUE (source, scheme_code, isin),
    UNIQUE (source, isin),
    FOREIGN KEY (source, scheme_code) REFERENCES public.tigzig_schemes(source, scheme_code)
);

CREATE TABLE public.tigzig_scheme_nav (
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    nav_date DATE NOT NULL,
    nav NUMERIC(24,10) NOT NULL CHECK (nav > 0 AND nav <> 'NaN'::numeric),
    upload_ts_raw TEXT,
    upload_ts_ist TIMESTAMP WITHOUT TIME ZONE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, scheme_code, nav_date),
    FOREIGN KEY (source, scheme_code) REFERENCES public.tigzig_schemes(source, scheme_code)
);

COMMENT ON COLUMN public.tigzig_scheme_nav.upload_ts_raw IS
    'Original AMFI publication text. TIGZIG documents IST wall-clock values even with a trailing Z; never parse that Z as UTC.';
COMMENT ON COLUMN public.tigzig_scheme_nav.upload_ts_ist IS
    'Optional parsed Asia/Kolkata wall-clock timestamp WITHOUT time zone; raw text remains authoritative.';

CREATE TABLE public.tigzig_scheme_aaum (
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    quarter_start DATE NOT NULL,
    quarter_end DATE NOT NULL,
    aaum_quarter TEXT NOT NULL CHECK (btrim(aaum_quarter) <> ''),
    aaum_lakhs NUMERIC(26,4) NOT NULL CHECK (aaum_lakhs >= 0 AND aaum_lakhs <> 'NaN'::numeric),
    aaum_cr NUMERIC(24,6) NOT NULL CHECK (aaum_cr >= 0 AND aaum_cr <> 'NaN'::numeric),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, scheme_code, quarter_end),
    FOREIGN KEY (source, scheme_code) REFERENCES public.tigzig_schemes(source, scheme_code),
    CHECK (quarter_start <= quarter_end),
    CHECK (aaum_cr = round(aaum_lakhs / 100, 6))
);

CREATE TABLE public.tigzig_sync_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('manual', 'scheduled', 'recovery', 'test')),
    job_type TEXT NOT NULL CHECK (job_type IN ('manifest', 'backfill', 'sync', 'revalidate')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'succeeded', 'failed', 'skipped', 'interrupted')),
    manifest_generated_at TIMESTAMPTZ,
    manifest_metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(manifest_metadata) = 'object'),
    files_processed JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(files_processed) = 'array'),
    records_read BIGINT NOT NULL DEFAULT 0 CHECK (records_read >= 0),
    inserted_count BIGINT NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
    updated_count BIGINT NOT NULL DEFAULT 0 CHECK (updated_count >= 0),
    skipped_count BIGINT NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
    rejected_count BIGINT NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    error_message TEXT,
    execution_duration_seconds NUMERIC(18,3) CHECK (execution_duration_seconds >= 0 AND execution_duration_seconds <> 'NaN'::numeric),
    CHECK (completed_at >= started_at),
    CHECK ((status = 'running' AND completed_at IS NULL AND execution_duration_seconds IS NULL) OR
           (status <> 'running' AND completed_at IS NOT NULL AND execution_duration_seconds IS NOT NULL))
);

CREATE TABLE public.tigzig_sync_file_versions (
    dataset_name TEXT PRIMARY KEY CHECK (dataset_name IN ('meta', 'nav', 'aaum')),
    filename TEXT NOT NULL CHECK (btrim(filename) <> ''),
    sha256 TEXT CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    manifest_generated_at TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_successful_import_at TIMESTAMPTZ,
    row_count BIGINT CHECK (row_count >= 0),
    last_import_run_id UUID REFERENCES public.tigzig_sync_runs(run_id),
    CHECK ((sha256 IS NULL AND last_successful_import_at IS NULL AND last_import_run_id IS NULL) OR
           (sha256 IS NOT NULL AND last_successful_import_at IS NOT NULL AND
            last_import_run_id IS NOT NULL AND manifest_generated_at IS NOT NULL AND row_count IS NOT NULL))
);

COMMENT ON TABLE public.tigzig_sync_file_versions IS
    'One successful-import checkpoint per dataset; never advance sha256 before committing dependent source writes. Run audit stores publication history.';

CREATE TABLE public.tigzig_ingestion_rejections (
    rejection_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES public.tigzig_sync_runs(run_id),
    dataset_name TEXT NOT NULL CHECK (dataset_name IN ('meta', 'nav', 'aaum')),
    raw_record JSONB NOT NULL,
    rejection_reason TEXT NOT NULL CHECK (btrim(rejection_reason) <> ''),
    resolution_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (resolution_status IN ('pending', 'resolved', 'ignored')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    CHECK ((resolution_status = 'pending' AND resolved_at IS NULL) OR
           (resolution_status <> 'pending' AND resolved_at IS NOT NULL))
);

CREATE TABLE public.app_funds (
    fund_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name TEXT NOT NULL CHECK (btrim(canonical_name) <> ''),
    slug TEXT NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'inactive')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.app_tigzig_fund_mapping (
    fund_id BIGINT NOT NULL REFERENCES public.app_funds(fund_id),
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    verification_method TEXT NOT NULL CHECK (btrim(verification_method) <> ''),
    verification_evidence JSONB NOT NULL CHECK (jsonb_typeof(verification_evidence) = 'object' AND
                                               verification_evidence <> '{}'::jsonb),
    verified_by TEXT NOT NULL CHECK (btrim(verified_by) <> ''),
    verified_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, scheme_code),
    FOREIGN KEY (source, scheme_code) REFERENCES public.tigzig_schemes(source, scheme_code)
);

COMMENT ON TABLE public.app_tigzig_fund_mapping IS
    'Verified mappings only. Many source schemes (plan/option variants) may belong to one canonical fund; one scheme belongs to at most one fund.';

CREATE INDEX idx_tigzig_schemes_fund_house ON public.tigzig_schemes(fund_house_id);
CREATE INDEX idx_tigzig_schemes_category ON public.tigzig_schemes(category_group_clean);
CREATE INDEX idx_tigzig_scheme_nav_date ON public.tigzig_scheme_nav(nav_date);
CREATE INDEX idx_tigzig_scheme_aaum_quarter ON public.tigzig_scheme_aaum(quarter_end);
CREATE INDEX idx_tigzig_sync_runs_started ON public.tigzig_sync_runs(started_at DESC);
CREATE INDEX idx_tigzig_sync_runs_failures ON public.tigzig_sync_runs(started_at DESC)
    WHERE status IN ('failed', 'interrupted');
CREATE INDEX idx_tigzig_versions_run ON public.tigzig_sync_file_versions(last_import_run_id);
CREATE INDEX idx_tigzig_rejections_run ON public.tigzig_ingestion_rejections(run_id);
CREATE INDEX idx_tigzig_rejections_pending ON public.tigzig_ingestion_rejections(created_at)
    WHERE resolution_status = 'pending';
CREATE INDEX idx_app_tigzig_mapping_fund ON public.app_tigzig_fund_mapping(fund_id);

-- updated_at is managed explicitly by future ingestion UPSERTs only on changes.
-- No update triggers: identical reimports must leave timestamps untouched.
