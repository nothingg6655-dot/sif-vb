-- VB-SIF database schema (PostgreSQL, public).
-- Run against an empty or existing VB-SIF database. Additive DDL: no DROP or table truncation.
-- For existing installations, audit custom column/constraint changes before migration.
-- Execute with: psql -v ON_ERROR_STOP=1 --single-transaction -d "VB-SIF" -f vb_sif_complete_schema.sql
SET search_path TO public;

CREATE TABLE IF NOT EXISTS sif_amcs (
    amc_id BIGSERIAL PRIMARY KEY,
    amc_name VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sif_schemes (
    scheme_code INTEGER PRIMARY KEY,
    temp_sif_code VARCHAR(30) NOT NULL UNIQUE,
    amc_id BIGINT NOT NULL REFERENCES sif_amcs(amc_id),
    scheme_name TEXT NOT NULL,
    scheme_type VARCHAR(150),
    category TEXT,
    category_sub VARCHAR(255),
    category_group_clean VARCHAR(150),
    category_group VARCHAR(255),
    scheme_plan VARCHAR(50),
    scheme_option VARCHAR(100),
    first_date DATE,
    last_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    is_stale BOOLEAN DEFAULT FALSE,
    txic_code VARCHAR(100),
    source VARCHAR(50),
    temp_plan_basis VARCHAR(100),
    temp_idcw_mode VARCHAR(100),
    temp_idcw_freq VARCHAR(100),
    temp_n_nav_days INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sif_scheme_isins (
    id BIGSERIAL PRIMARY KEY,
    scheme_code INTEGER NOT NULL REFERENCES sif_schemes(scheme_code),
    isin VARCHAR(20) NOT NULL,
    isin_type VARCHAR(20) NOT NULL CHECK (isin_type IN ('PRIMARY', 'SECONDARY')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (scheme_code, isin),
    UNIQUE (scheme_code, isin_type)
);

CREATE TABLE IF NOT EXISTS sif_nav_history (
    id BIGSERIAL PRIMARY KEY,
    scheme_code INTEGER NOT NULL REFERENCES sif_schemes(scheme_code),
    nav_date DATE NOT NULL,
    nav NUMERIC(18,6) NOT NULL CHECK (nav >= 0),
    source_upload_ts TIMESTAMP,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (scheme_code, nav_date)
);

CREATE TABLE IF NOT EXISTS sif_aaum_history (
    id BIGSERIAL PRIMARY KEY,
    scheme_code INTEGER NOT NULL REFERENCES sif_schemes(scheme_code),
    aaum_quarter VARCHAR(20),
    quarter_start DATE NOT NULL,
    quarter_end DATE NOT NULL,
    aaum_cr NUMERIC(20,6) NOT NULL CHECK (aaum_cr >= 0),
    aaum_lakhs NUMERIC(22,4) CHECK (aaum_lakhs >= 0),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (scheme_code, quarter_end),
    CHECK (quarter_start <= quarter_end)
);

CREATE INDEX IF NOT EXISTS idx_sif_schemes_amc ON sif_schemes(amc_id);
CREATE INDEX IF NOT EXISTS idx_sif_schemes_category ON sif_schemes(category_group_clean);
CREATE INDEX IF NOT EXISTS idx_sif_schemes_plan_option ON sif_schemes(scheme_plan, scheme_option);
CREATE INDEX IF NOT EXISTS idx_sif_schemes_active ON sif_schemes(is_active);
CREATE INDEX IF NOT EXISTS idx_sif_nav_date ON sif_nav_history(nav_date DESC);
CREATE INDEX IF NOT EXISTS idx_sif_aaum_quarter ON sif_aaum_history(quarter_end DESC);
CREATE INDEX IF NOT EXISTS idx_sif_isins_isin ON sif_scheme_isins(isin);

CREATE OR REPLACE VIEW vw_sif_scheme_latest AS
SELECT
    s.scheme_code,
    s.temp_sif_code,
    s.scheme_name,
    a.amc_name,
    s.category,
    s.category_sub,
    s.scheme_plan,
    s.scheme_option,
    s.is_active,
    n.nav_date,
    n.nav,
    q.aaum_quarter,
    q.quarter_end AS aaum_quarter_end,
    q.aaum_cr,
    q.aaum_lakhs
FROM sif_schemes s
JOIN sif_amcs a ON a.amc_id = s.amc_id
LEFT JOIN LATERAL (
    SELECT nav_date, nav
    FROM sif_nav_history
    WHERE scheme_code = s.scheme_code
    ORDER BY nav_date DESC
    LIMIT 1
) n ON TRUE
LEFT JOIN LATERAL (
    SELECT aaum_quarter, quarter_end, aaum_cr, aaum_lakhs
    FROM sif_aaum_history
    WHERE scheme_code = s.scheme_code
    ORDER BY quarter_end DESC
    LIMIT 1
) q ON TRUE;


-- ================================================================
-- LAYER 2: CANONICAL STRATEGY (FUND) IDENTITY AND RECONCILIATION
-- ================================================================
-- Do not treat each Direct/Regular/Growth/IDCW scheme as a different fund.
-- A strategy belongs to one AMC; many scheme codes belong to that strategy.
-- Factsheet AMC display names may differ from Tigzig AMC display names.

CREATE TABLE IF NOT EXISTS sif_amc_aliases (
    alias_id BIGSERIAL PRIMARY KEY,
    amc_id BIGINT NOT NULL REFERENCES sif_amcs(amc_id),
    source_system VARCHAR(50) NOT NULL,
    reported_name TEXT NOT NULL,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_system, reported_name)
);

CREATE TABLE IF NOT EXISTS sif_strategies (
    strategy_id BIGSERIAL PRIMARY KEY,
    amc_id BIGINT NOT NULL REFERENCES sif_amcs(amc_id),
    strategy_name TEXT NOT NULL,
    slug VARCHAR(240) NOT NULL,
    strategy_category TEXT,
    strategy_subcategory TEXT,
    structure_type VARCHAR(120),
    launch_date DATE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    website_visible BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (amc_id, slug)
);

CREATE TABLE IF NOT EXISTS sif_strategy_scheme_links (
    scheme_code INTEGER PRIMARY KEY REFERENCES sif_schemes(scheme_code),
    strategy_id BIGINT NOT NULL REFERENCES sif_strategies(strategy_id),
    mapping_method VARCHAR(30) NOT NULL DEFAULT 'MANUAL'
        CHECK (mapping_method IN ('MANUAL','EXTERNAL_ID','ISIN','NAME_CANDIDATE')),
    review_status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING','VERIFIED','REJECTED')),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((review_status <> 'VERIFIED') OR reviewed_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS sif_strategy_external_identifiers (
    identifier_id BIGSERIAL PRIMARY KEY,
    strategy_id BIGINT NOT NULL REFERENCES sif_strategies(strategy_id),
    source_system VARCHAR(50) NOT NULL,
    external_id TEXT NOT NULL,
    source_display_name TEXT,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (source_system, external_id)
);

CREATE OR REPLACE FUNCTION sif_validate_link_amc() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE s_amc BIGINT; f_amc BIGINT;
BEGIN
    SELECT amc_id INTO s_amc FROM sif_schemes WHERE scheme_code = NEW.scheme_code;
    SELECT amc_id INTO f_amc FROM sif_strategies WHERE strategy_id = NEW.strategy_id;
    IF s_amc IS DISTINCT FROM f_amc THEN
        RAISE EXCEPTION 'Scheme % and strategy % have different amc_id; reconcile AMC identity before linking', NEW.scheme_code, NEW.strategy_id;
    END IF;
    RETURN NEW;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger
                   WHERE tgname='trg_sif_link_amc'
                     AND tgrelid='public.sif_strategy_scheme_links'::regclass) THEN
        CREATE TRIGGER trg_sif_link_amc BEFORE INSERT OR UPDATE
        ON sif_strategy_scheme_links FOR EACH ROW EXECUTE FUNCTION sif_validate_link_amc();
    END IF;
END $$;

-- ================================================================
-- LAYER 3: SOURCE REGISTRY, INGESTION LINEAGE AND DATA QUALITY
-- ================================================================

CREATE TABLE IF NOT EXISTS sif_data_sources (
    source_code VARCHAR(50) PRIMARY KEY,
    display_name TEXT NOT NULL,
    source_type VARCHAR(20) NOT NULL
        CHECK (source_type IN ('API','CSV','PDF','WEB','CALCULATED','MANUAL')),
    authoritative_for TEXT,
    base_url TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO sif_data_sources (source_code,display_name,source_type,authoritative_for) VALUES
    ('TIGZIG','Tigzig SIF datasets','CSV','scheme metadata, daily NAV, quarterly average AUM'),
    ('OFFICIAL_FACTSHEET','Official AMC factsheets','PDF','published portfolios and strategy metrics'),
    ('BENCHMARK','Licensed/official benchmark feed','API','benchmark index history'),
    ('DERIVED','VB-SIF calculation engine','CALCULATED','scheme performance and analytics')
ON CONFLICT (source_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS sif_ingestion_runs (
    run_id BIGSERIAL PRIMARY KEY,
    source_code VARCHAR(50) NOT NULL REFERENCES sif_data_sources(source_code),
    dataset VARCHAR(80) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('RUNNING','SUCCEEDED','PARTIAL','FAILED','CANCELLED')),
    fetched_count INTEGER NOT NULL DEFAULT 0 CHECK (fetched_count >= 0),
    inserted_count INTEGER NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
    updated_count INTEGER NOT NULL DEFAULT 0 CHECK (updated_count >= 0),
    rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    error_summary TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS sif_ingestion_files (
    file_id BIGSERIAL PRIMARY KEY,
    source_code VARCHAR(50) NOT NULL REFERENCES sif_data_sources(source_code),
    run_id BIGINT REFERENCES sif_ingestion_runs(run_id),
    file_name TEXT,
    original_url TEXT,
    storage_uri TEXT,
    sha256 CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-fA-F]{64}$'),
    mime_type VARCHAR(120),
    bytes_size BIGINT CHECK (bytes_size >= 0),
    retrieved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_code,sha256)
);

CREATE TABLE IF NOT EXISTS sif_ingestion_rejections (
    rejection_id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES sif_ingestion_runs(run_id),
    file_id BIGINT REFERENCES sif_ingestion_files(file_id),
    source_code VARCHAR(50) NOT NULL REFERENCES sif_data_sources(source_code),
    source_row_key TEXT,
    entity_kind VARCHAR(80),
    error_code VARCHAR(80) NOT NULL,
    error_message TEXT NOT NULL,
    raw_row JSONB NOT NULL,
    fixed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Source run linkage is optional for old rows; new loads should fill it.
ALTER TABLE sif_nav_history ADD COLUMN IF NOT EXISTS ingestion_run_id BIGINT REFERENCES sif_ingestion_runs(run_id);
ALTER TABLE sif_aaum_history ADD COLUMN IF NOT EXISTS ingestion_run_id BIGINT REFERENCES sif_ingestion_runs(run_id);
ALTER TABLE sif_schemes ADD COLUMN IF NOT EXISTS ingestion_run_id BIGINT REFERENCES sif_ingestion_runs(run_id);

CREATE TABLE IF NOT EXISTS sif_data_quality_issues (
    issue_id BIGSERIAL PRIMARY KEY,
    source_code VARCHAR(50) REFERENCES sif_data_sources(source_code),
    run_id BIGINT REFERENCES sif_ingestion_runs(run_id),
    strategy_id BIGINT REFERENCES sif_strategies(strategy_id),
    scheme_code INTEGER REFERENCES sif_schemes(scheme_code),
    issue_type VARCHAR(100) NOT NULL,
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('INFO','WARNING','ERROR','CRITICAL')),
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','RESOLVED','DISMISSED')),
    message TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ================================================================
-- LAYER 4: BENCHMARKS AND HISTORICAL RETURNS
-- ================================================================

CREATE TABLE IF NOT EXISTS sif_benchmarks (
    benchmark_id BIGSERIAL PRIMARY KEY,
    benchmark_name TEXT NOT NULL UNIQUE,
    benchmark_code VARCHAR(80) UNIQUE,
    currency_code CHAR(3) NOT NULL DEFAULT 'INR',
    methodology TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sif_benchmark_history (
    benchmark_id BIGINT NOT NULL REFERENCES sif_benchmarks(benchmark_id),
    value_date DATE NOT NULL,
    index_value NUMERIC(22,8) NOT NULL CHECK (index_value > 0),
    source_code VARCHAR(50) REFERENCES sif_data_sources(source_code),
    PRIMARY KEY (benchmark_id, value_date)
);

-- ================================================================
-- LAYER 5: OFFICIAL PDF DOCUMENTS AND EXTRACTED REPORTS
-- ================================================================
-- A PDF may contain multiple strategies. A strategy may appear in multiple
-- PDF editions. A report = one strategy within one PDF edition.
-- raw_extraction_json keeps ALL fields, even not yet normalized or empty.

CREATE TABLE IF NOT EXISTS sif_factsheet_documents (
    document_id BIGSERIAL PRIMARY KEY,
    amc_id BIGINT NOT NULL REFERENCES sif_amcs(amc_id),
    ingestion_file_id BIGINT REFERENCES sif_ingestion_files(file_id),
    publishing_page_url TEXT,
    official_pdf_url TEXT,
    local_storage_uri TEXT,
    sha256 CHAR(64) NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-fA-F]{64}$'),
    reporting_month DATE CHECK (reporting_month IS NULL OR EXTRACT(DAY FROM reporting_month) = 1),
    edition_name TEXT,
    official_only BOOLEAN,
    latest_listing_verified BOOLEAN,
    downloaded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sif_factsheet_reports (
    report_id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES sif_factsheet_documents(document_id),
    strategy_id BIGINT NOT NULL REFERENCES sif_strategies(strategy_id),
    report_month DATE NOT NULL CHECK (EXTRACT(DAY FROM report_month) = 1),
    portfolio_as_of DATE,
    section_key TEXT NOT NULL DEFAULT 'MAIN',
    plan_edition VARCHAR(30) NOT NULL DEFAULT 'COMBINED'
        CHECK (plan_edition IN ('COMBINED','DIRECT','REGULAR','OTHER')),
    published_scheme_name TEXT NOT NULL,
    source_pages INTEGER[],
    annexure_pages INTEGER[],
    portfolio_scope TEXT,
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    extraction_adapter TEXT,
    validation_status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (validation_status IN ('PENDING','PASSED','WARNING','FAILED')),
    validation_checks JSONB NOT NULL DEFAULT '{}'::jsonb,
    validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    validation_warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_extraction_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (document_id,strategy_id,section_key,plan_edition)
);

-- Only one designated published report for a strategy/month.
CREATE UNIQUE INDEX IF NOT EXISTS uq_sif_primary_strategy_month
ON sif_factsheet_reports(strategy_id,report_month) WHERE is_primary;

CREATE TABLE IF NOT EXISTS sif_factsheet_pages (
    document_id BIGINT NOT NULL REFERENCES sif_factsheet_documents(document_id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    extracted_text TEXT,
    extracted_links JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY(document_id,page_number)
);

-- Historical strategy details by factsheet report. Do not overwrite past snapshots.
CREATE TABLE IF NOT EXISTS sif_report_strategy_profile (
    report_id BIGINT PRIMARY KEY REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    investment_objective TEXT,
    launch_date DATE,
    benchmark_id BIGINT REFERENCES sif_benchmarks(benchmark_id),
    published_benchmark TEXT,
    load_structure TEXT,
    minimum_investment_inr NUMERIC(20,2) CHECK (minimum_investment_inr >= 0),
    accredited_minimum_inr NUMERIC(20,2) CHECK (accredited_minimum_inr >= 0),
    subscription_redemption_frequency TEXT,
    taxation TEXT,
    portfolio_turnover_ratio NUMERIC(20,8),
    strategy_description TEXT,
    supplementary_fields JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS sif_fund_managers (
    manager_id BIGSERIAL PRIMARY KEY,
    manager_name TEXT NOT NULL UNIQUE,
    biography TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sif_report_fund_managers (
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    manager_id BIGINT NOT NULL REFERENCES sif_fund_managers(manager_id),
    manager_role TEXT NOT NULL DEFAULT 'Fund Manager',
    experience_text TEXT,
    managing_since DATE,
    published_footnote TEXT,
    supplementary_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (report_id,manager_id,manager_role)
);

-- Published net AUM / monthly avg AUM is NOT quarterly scheme AAUM.
CREATE TABLE IF NOT EXISTS sif_report_aum (
    report_id BIGINT PRIMARY KEY REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    aum_as_of DATE,
    net_aum_cr NUMERIC(22,6) CHECK (net_aum_cr >= 0),
    monthly_average_aum_cr NUMERIC(22,6) CHECK (monthly_average_aum_cr >= 0),
    supplementary_fields JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Label must be kept verbatim; ambiguous 'Regular Plan' is NOT
-- automatically classified as Growth without verified scheme mapping.
CREATE TABLE IF NOT EXISTS sif_report_nav (
    reported_nav_id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    published_label TEXT NOT NULL,
    scheme_code INTEGER REFERENCES sif_schemes(scheme_code),
    plan_type VARCHAR(20) CHECK (plan_type IN ('DIRECT','REGULAR')),
    option_type VARCHAR(30) CHECK (option_type IN ('GROWTH','IDCW','OTHER')),
    nav_date DATE,
    nav NUMERIC(18,6) NOT NULL CHECK (nav > 0),
    UNIQUE (report_id,published_label)
);

CREATE TABLE IF NOT EXISTS sif_report_expense_ratios (
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    plan_type VARCHAR(20) NOT NULL CHECK (plan_type IN ('DIRECT','REGULAR','OTHER')),
    ratio_type VARCHAR(30) NOT NULL CHECK (ratio_type IN ('BASE','TER','OTHER')),
    ratio_percent NUMERIC(12,6) NOT NULL CHECK (ratio_percent >= 0),
    as_of_date DATE,
    qualification_note TEXT,
    PRIMARY KEY(report_id,plan_type,ratio_type)
);

-- ================================================================
-- LAYER 6: INSTRUMENT HOLDINGS AND PUBLISHED PORTFOLIO DATA
-- ================================================================

CREATE TABLE IF NOT EXISTS sif_instruments (
    instrument_id BIGSERIAL PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    isin VARCHAR(20) UNIQUE,
    exchange_symbol VARCHAR(80),
    instrument_type VARCHAR(70),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sif_report_holdings (
    holding_id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    row_number INTEGER NOT NULL CHECK (row_number > 0),
    portfolio_section VARCHAR(50) NOT NULL DEFAULT 'MAIN',
    instrument_id BIGINT REFERENCES sif_instruments(instrument_id),
    published_instrument_name TEXT NOT NULL,
    asset_class TEXT,
    category_name TEXT,
    sector_name TEXT,
    market_cap_class TEXT,
    rating TEXT,
    rating_classification TEXT,
    derivative_type TEXT,
    quantity NUMERIC(28,8),
    market_value_inr_lakhs NUMERIC(26,6),
    percent_to_net_assets NUMERIC(16,8),
    derivative_exposure_pct NUMERIC(16,8),
    hedged_exposure_pct NUMERIC(16,8),
    unhedged_exposure_pct NUMERIC(16,8),
    published_net_exposure_pct NUMERIC(16,8),
    is_aggregate BOOLEAN NOT NULL DEFAULT FALSE,
    source_page INTEGER,
    published_precision_marker TEXT,
    supplementary_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(report_id,portfolio_section,row_number)
);

-- All published allocation / total families in one constrained long-form table.
-- Preserve the statement as published: reported sector weights sometimes
-- have a rounding difference vs the sum of individual displayed securities.
CREATE TABLE IF NOT EXISTS sif_report_allocations (
    allocation_id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    allocation_family VARCHAR(50) NOT NULL CHECK (allocation_family IN (
        'PORTFOLIO_TOTAL','SECTOR','MARKET_CAP','ASSET','RATING',
        'PORTFOLIO_SUMMARY','DERIVATIVE_TOTAL','PORTFOLIO_AGGREGATE',
        'DEBT_CATEGORY','OTHER_EXPOSURE','CASH_AND_DERIVATIVES'
    )),
    bucket_label TEXT NOT NULL,
    exposure_percent NUMERIC(16,8),
    metric_value NUMERIC(28,8),
    metric_unit VARCHAR(50),
    published_order INTEGER,
    source_page INTEGER,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(report_id,allocation_family,bucket_label)
);

CREATE TABLE IF NOT EXISTS sif_report_statistical_indicators (
    indicator_id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    family VARCHAR(60) NOT NULL CHECK (family IN (
        'STATISTICAL_DEBT','DEBT_PORTFOLIO_QUANT','PORTFOLIO_QUANT',
        'PUBLISHED_PORTFOLIO_QUANT','OTHER'
    )),
    indicator_name TEXT NOT NULL,
    numeric_value NUMERIC(28,10),
    text_value TEXT,
    unit VARCHAR(40),
    source_page INTEGER,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(report_id,family,indicator_name)
);

CREATE TABLE IF NOT EXISTS sif_report_risk_bands (
    report_id BIGINT PRIMARY KEY REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    investment_strategy_risk_band TEXT,
    benchmark_risk_band TEXT,
    benchmark_name TEXT,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- PUBLISHED performance is distinct from ENGINE-CALCULATED performance.
CREATE TABLE IF NOT EXISTS sif_report_published_performance (
    published_performance_id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    scheme_code INTEGER REFERENCES sif_schemes(scheme_code),
    performance_family VARCHAR(30) NOT NULL CHECK (performance_family IN (
        'SCHEME','MONTHLY_ROLLING','BENCHMARK','OTHER'
    )),
    published_period_label TEXT,
    plan_label TEXT,
    option_label TEXT,
    period_start DATE,
    period_end DATE,
    scheme_return_pct NUMERIC(16,8),
    benchmark_return_pct NUMERIC(16,8),
    additional_values JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS sif_report_idcw_history (
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    event_date DATE NOT NULL,
    plan_label TEXT NOT NULL,
    amount_per_unit_inr NUMERIC(18,6) CHECK (amount_per_unit_inr >= 0),
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(report_id,event_date,plan_label)
);

CREATE TABLE IF NOT EXISTS sif_report_notes (
    note_id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    note_family VARCHAR(70) NOT NULL,
    note_text TEXT NOT NULL,
    source_page INTEGER,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS sif_report_external_field_links (
    link_id BIGSERIAL PRIMARY KEY,
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    published_field TEXT,
    target_field TEXT,
    source_name TEXT,
    external_url TEXT,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS sif_report_field_availability (
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    field_path TEXT NOT NULL,
    availability_status VARCHAR(30) NOT NULL CHECK (availability_status IN (
        'PUBLISHED','NOT_PUBLISHED','MISSING','PARSE_FAILED','NOT_APPLICABLE','UNKNOWN'
    )),
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(report_id,field_path)
);

-- Keep future AMC-specific fields, corrections, structured rows not yet mapped.
CREATE TABLE IF NOT EXISTS sif_report_extension_data (
    report_id BIGINT NOT NULL REFERENCES sif_factsheet_reports(report_id) ON DELETE CASCADE,
    field_group TEXT NOT NULL,
    field_key TEXT NOT NULL,
    raw_value JSONB NOT NULL,
    PRIMARY KEY (report_id,field_group,field_key)
);

-- ================================================================
-- LAYER 7: SCHEME DISTRIBUTIONS AND DERIVED PERFORMANCE ANALYTICS
-- ================================================================

CREATE TABLE IF NOT EXISTS sif_scheme_idcw_events (
    event_id BIGSERIAL PRIMARY KEY,
    scheme_code INTEGER NOT NULL REFERENCES sif_schemes(scheme_code),
    ex_date DATE NOT NULL,
    record_date DATE,
    payment_date DATE,
    idcw_per_unit_inr NUMERIC(18,6) NOT NULL CHECK (idcw_per_unit_inr >= 0),
    source_code VARCHAR(50) REFERENCES sif_data_sources(source_code),
    UNIQUE (scheme_code,ex_date)
);

-- Derived cache can be regenerated from original NAV/IDCW data.
-- Return calculation, dividends and annualisation methodology are versioned.
CREATE TABLE IF NOT EXISTS sif_scheme_return_cache (
    scheme_code INTEGER NOT NULL REFERENCES sif_schemes(scheme_code),
    as_of_date DATE NOT NULL,
    horizon VARCHAR(30) NOT NULL,
    method VARCHAR(30) NOT NULL CHECK (method IN ('ABSOLUTE','CAGR','XIRR','TOTAL_RETURN')),
    calculation_version VARCHAR(40) NOT NULL,
    start_nav_date DATE,
    end_nav_date DATE,
    start_nav NUMERIC(18,6),
    end_nav NUMERIC(18,6),
    return_pct NUMERIC(18,8),
    benchmark_id BIGINT REFERENCES sif_benchmarks(benchmark_id),
    benchmark_return_pct NUMERIC(18,8),
    observation_count INTEGER CHECK (observation_count >= 0),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(scheme_code,as_of_date,horizon,method,calculation_version)
);

CREATE TABLE IF NOT EXISTS sif_scheme_risk_cache (
    scheme_code INTEGER NOT NULL REFERENCES sif_schemes(scheme_code),
    as_of_date DATE NOT NULL,
    lookback_days INTEGER NOT NULL CHECK (lookback_days > 0),
    calculation_version VARCHAR(40) NOT NULL,
    annualized_volatility_pct NUMERIC(18,8),
    downside_deviation_pct NUMERIC(18,8),
    sharpe_ratio NUMERIC(18,8),
    sortino_ratio NUMERIC(18,8),
    max_drawdown_pct NUMERIC(18,8),
    beta NUMERIC(18,8),
    alpha_pct NUMERIC(18,8),
    benchmark_id BIGINT REFERENCES sif_benchmarks(benchmark_id),
    observation_count INTEGER CHECK (observation_count >= 0),
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(scheme_code,as_of_date,lookback_days,calculation_version)
);

-- ================================================================
-- LAYER 8: OPTIONAL WEBSITE FEATURES (WATCHLIST + COMPARE)
-- ================================================================
-- user_id can be mapped from the site's existing authentication system.

CREATE TABLE IF NOT EXISTS sif_site_users (
    user_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sif_user_watchlists (
    watchlist_id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES sif_site_users(user_id) ON DELETE CASCADE,
    watchlist_name VARCHAR(120) NOT NULL DEFAULT 'My Watchlist',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id,watchlist_name)
);

CREATE TABLE IF NOT EXISTS sif_user_watchlist_items (
    watchlist_id BIGINT NOT NULL REFERENCES sif_user_watchlists(watchlist_id) ON DELETE CASCADE,
    scheme_code INTEGER NOT NULL REFERENCES sif_schemes(scheme_code),
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(watchlist_id,scheme_code)
);

CREATE TABLE IF NOT EXISTS sif_user_comparisons (
    comparison_id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES sif_site_users(user_id) ON DELETE CASCADE,
    comparison_name TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sif_user_comparison_items (
    comparison_id BIGINT NOT NULL REFERENCES sif_user_comparisons(comparison_id) ON DELETE CASCADE,
    scheme_code INTEGER NOT NULL REFERENCES sif_schemes(scheme_code),
    display_order INTEGER NOT NULL DEFAULT 1 CHECK(display_order > 0),
    PRIMARY KEY(comparison_id,scheme_code),
    UNIQUE(comparison_id,display_order)
);

-- ================================================================
-- LAYER 9: INDEXES AND READ MODELS
-- ================================================================
CREATE INDEX IF NOT EXISTS idx_sif_amc_aliases_amc ON sif_amc_aliases(amc_id);
CREATE INDEX IF NOT EXISTS idx_sif_strategies_amc ON sif_strategies(amc_id);
CREATE INDEX IF NOT EXISTS idx_sif_strategies_category ON sif_strategies(strategy_category);
CREATE INDEX IF NOT EXISTS idx_sif_strategies_visible ON sif_strategies(website_visible) WHERE website_visible;
CREATE INDEX IF NOT EXISTS idx_sif_strategy_links_strategy ON sif_strategy_scheme_links(strategy_id);
CREATE INDEX IF NOT EXISTS idx_sif_strategy_links_verified ON sif_strategy_scheme_links(strategy_id,review_status);
CREATE INDEX IF NOT EXISTS idx_sif_ingestion_runs_status ON sif_ingestion_runs(source_code,dataset,started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sif_ingestion_rejections_open ON sif_ingestion_rejections(source_code,created_at DESC) WHERE fixed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_sif_dq_open ON sif_data_quality_issues(severity,created_at DESC) WHERE status='OPEN';
CREATE INDEX IF NOT EXISTS idx_sif_factsheet_docs_month ON sif_factsheet_documents(amc_id,reporting_month DESC);
CREATE INDEX IF NOT EXISTS idx_sif_reports_strategy_month ON sif_factsheet_reports(strategy_id,report_month DESC);
CREATE INDEX IF NOT EXISTS idx_sif_reports_document ON sif_factsheet_reports(document_id);
CREATE INDEX IF NOT EXISTS idx_sif_report_manager_id ON sif_report_fund_managers(manager_id);
CREATE INDEX IF NOT EXISTS idx_sif_report_holdings_weight ON sif_report_holdings(report_id,percent_to_net_assets DESC);
CREATE INDEX IF NOT EXISTS idx_sif_report_holdings_instrument ON sif_report_holdings(instrument_id);
CREATE INDEX IF NOT EXISTS idx_sif_report_holdings_sector ON sif_report_holdings(report_id,sector_name);
CREATE INDEX IF NOT EXISTS idx_sif_report_allocations_family ON sif_report_allocations(report_id,allocation_family);
CREATE INDEX IF NOT EXISTS idx_sif_report_performance_family ON sif_report_published_performance(report_id,performance_family);
CREATE INDEX IF NOT EXISTS idx_sif_return_horizon ON sif_scheme_return_cache(as_of_date,horizon,method);
CREATE INDEX IF NOT EXISTS idx_sif_risk_measure_date ON sif_scheme_risk_cache(as_of_date);
CREATE INDEX IF NOT EXISTS idx_sif_report_json_gin ON sif_factsheet_reports USING GIN(raw_extraction_json jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_sif_nav_scheme_latest ON sif_nav_history(scheme_code,nav_date DESC);
CREATE INDEX IF NOT EXISTS idx_sif_aaum_scheme_latest ON sif_aaum_history(scheme_code,quarter_end DESC);
CREATE INDEX IF NOT EXISTS idx_sif_watchlist_user ON sif_user_watchlists(user_id);

-- Verified links only: candidates must never appear as established fund membership.
CREATE OR REPLACE VIEW vw_sif_verified_strategy_schemes AS
SELECT st.strategy_id,st.strategy_name,st.slug,st.amc_id,
       s.scheme_code,s.temp_sif_code,s.scheme_name,s.scheme_plan,
       s.scheme_option,s.is_active,s.first_date,s.last_date
FROM sif_strategies st
JOIN sif_strategy_scheme_links lk ON lk.strategy_id=st.strategy_id AND lk.review_status='VERIFIED'
JOIN sif_schemes s ON s.scheme_code=lk.scheme_code;

-- Choose the verified designated edition, not simply any most recently downloaded PDF.
CREATE OR REPLACE VIEW vw_sif_strategy_latest_report AS
SELECT st.strategy_id,st.strategy_name,st.slug,st.amc_id,
       rpt.report_id,rpt.report_month,rpt.portfolio_as_of,
       rpt.published_scheme_name,rpt.validation_status,
       pr.investment_objective,pr.published_benchmark,
       pr.minimum_investment_inr,pr.accredited_minimum_inr,
       a.net_aum_cr,a.monthly_average_aum_cr,
       rb.investment_strategy_risk_band,rb.benchmark_risk_band
FROM sif_strategies st
LEFT JOIN LATERAL (
    SELECT r.* FROM sif_factsheet_reports r
    JOIN sif_factsheet_documents fd ON fd.document_id=r.document_id
    WHERE r.strategy_id=st.strategy_id AND r.is_primary
      AND fd.official_only IS TRUE
      AND r.validation_status IN ('PASSED','WARNING')
    ORDER BY r.report_month DESC,r.extracted_at DESC,r.report_id DESC
    LIMIT 1
) rpt ON TRUE
LEFT JOIN sif_report_strategy_profile pr ON pr.report_id=rpt.report_id
LEFT JOIN sif_report_aum a ON a.report_id=rpt.report_id
LEFT JOIN sif_report_risk_bands rb ON rb.report_id=rpt.report_id;

CREATE OR REPLACE VIEW vw_sif_strategy_directory AS
SELECT st.strategy_id,st.strategy_name,st.slug,st.strategy_category,
       st.strategy_subcategory,st.is_active,st.website_visible,
       amc.amc_name, lr.report_month,lr.portfolio_as_of,
       lr.net_aum_cr,lr.monthly_average_aum_cr,
       lr.published_benchmark,lr.investment_strategy_risk_band,
       dir.scheme_code AS direct_growth_scheme_code,
       latest.nav_date AS direct_growth_nav_date,
       latest.nav AS direct_growth_nav
FROM sif_strategies st
JOIN sif_amcs amc ON amc.amc_id=st.amc_id
LEFT JOIN vw_sif_strategy_latest_report lr ON lr.strategy_id=st.strategy_id
LEFT JOIN LATERAL (
    SELECT ss.scheme_code FROM vw_sif_verified_strategy_schemes ss
    WHERE ss.strategy_id=st.strategy_id AND UPPER(ss.scheme_plan)='DIRECT'
      AND UPPER(ss.scheme_option)='GROWTH'
    ORDER BY ss.scheme_code LIMIT 1
) dir ON TRUE
LEFT JOIN LATERAL (
    SELECT nav_date,nav FROM sif_nav_history
    WHERE scheme_code=dir.scheme_code ORDER BY nav_date DESC LIMIT 1
) latest ON TRUE;

CREATE OR REPLACE VIEW vw_sif_source_freshness AS
SELECT d.source_code,d.dataset,MAX(d.started_at) AS last_attempt_at,
       MAX(d.finished_at) FILTER(WHERE d.status='SUCCEEDED') AS last_success_at,
       COUNT(*) FILTER(WHERE d.status='FAILED') AS failed_runs
FROM sif_ingestion_runs d GROUP BY d.source_code,d.dataset;

-- ================================================================
-- LAYER 10: TIMESTAMP MAINTENANCE
-- ================================================================
CREATE OR REPLACE FUNCTION sif_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at=NOW(); RETURN NEW; END $$;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['sif_amcs','sif_schemes','sif_nav_history','sif_aaum_history','sif_strategies']
    LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_trigger
                      WHERE tgname='trg_'||t||'_updated_at'
                        AND tgrelid=to_regclass('public.'||t)) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION sif_touch_updated_at()',
                'trg_'||t||'_updated_at',t);
        END IF;
    END LOOP;
END $$;

-- End of schema. Load metadata FIRST; then NAV / AAUM; then verify strategy links;
-- then import official PDFs + report children. Never seed fictional metrics.
