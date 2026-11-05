-- M6: canonical fund mapping, ticker selection, and calculated read-model storage.

ALTER TABLE public.app_tigzig_fund_mapping
    ADD CONSTRAINT uq_app_mapping_fund_scheme UNIQUE (fund_id, source, scheme_code);

CREATE TABLE public.app_tigzig_mapping_reviews (
    review_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    proposed_fund_key TEXT,
    reason_code TEXT NOT NULL CHECK (btrim(reason_code) <> ''),
    evidence JSONB NOT NULL CHECK (jsonb_typeof(evidence) = 'object'),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'resolved')),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, scheme_code),
    FOREIGN KEY (source, scheme_code)
        REFERENCES public.tigzig_schemes(source, scheme_code),
    CHECK ((status = 'pending' AND reviewed_at IS NULL) OR
           (status <> 'pending' AND reviewed_at IS NOT NULL AND btrim(reviewed_by) <> ''))
);

CREATE TABLE public.app_ticker_config (
    fund_id BIGINT PRIMARY KEY REFERENCES public.app_funds(fund_id),
    preferred_source TEXT NOT NULL,
    preferred_scheme_code BIGINT NOT NULL,
    display_order INTEGER NOT NULL UNIQUE CHECK (display_order > 0),
    is_visible BOOLEAN NOT NULL DEFAULT TRUE,
    selection_verified BOOLEAN NOT NULL DEFAULT FALSE,
    selection_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(selection_evidence) = 'object'),
    verified_by TEXT,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (fund_id, preferred_source, preferred_scheme_code)
        REFERENCES public.app_tigzig_fund_mapping(fund_id, source, scheme_code),
    CHECK ((selection_verified AND verified_at IS NOT NULL AND btrim(verified_by) <> ''
            AND selection_evidence <> '{}'::jsonb) OR
           (NOT selection_verified AND verified_at IS NULL AND verified_by IS NULL))
);

CREATE TABLE public.app_monthly_returns (
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    month_end DATE NOT NULL,
    start_nav_date DATE NOT NULL,
    end_nav_date DATE NOT NULL,
    start_nav NUMERIC(24,10) NOT NULL CHECK (start_nav > 0),
    end_nav NUMERIC(24,10) NOT NULL CHECK (end_nav > 0),
    absolute_return_pct NUMERIC(18,8) NOT NULL,
    is_partial_month BOOLEAN NOT NULL DEFAULT FALSE,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, scheme_code, month_end),
    FOREIGN KEY (source, scheme_code)
        REFERENCES public.tigzig_schemes(source, scheme_code),
    CHECK (start_nav_date <= end_nav_date AND end_nav_date <= month_end)
);

CREATE TABLE public.app_performance_cache (
    source TEXT NOT NULL,
    scheme_code BIGINT NOT NULL,
    as_of_date DATE NOT NULL,
    latest_nav NUMERIC(24,10) NOT NULL CHECK (latest_nav > 0),
    daily_return_pct NUMERIC(18,8),
    one_month_return_pct NUMERIC(18,8),
    three_month_return_pct NUMERIC(18,8),
    since_inception_return_pct NUMERIC(18,8),
    calculation_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(calculation_metadata) = 'object'),
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, scheme_code),
    FOREIGN KEY (source, scheme_code)
        REFERENCES public.tigzig_schemes(source, scheme_code)
);

CREATE TABLE public.app_external_identifiers (
    external_identifier_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fund_id BIGINT NOT NULL REFERENCES public.app_funds(fund_id),
    namespace TEXT NOT NULL CHECK (btrim(namespace) <> ''),
    identifier_value TEXT NOT NULL CHECK (btrim(identifier_value) <> ''),
    source TEXT NOT NULL CHECK (btrim(source) <> ''),
    source_scheme_code BIGINT,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    verification_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(verification_evidence) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace, identifier_value),
    FOREIGN KEY (source, source_scheme_code)
        REFERENCES public.tigzig_schemes(source, scheme_code),
    CHECK (source_scheme_code IS NOT NULL OR source <> 'TIGZIG'),
    CHECK (NOT verified OR verification_evidence <> '{}'::jsonb)
);

CREATE INDEX idx_app_mapping_reviews_pending
    ON public.app_tigzig_mapping_reviews(created_at) WHERE status = 'pending';
CREATE INDEX idx_app_ticker_visible_order
    ON public.app_ticker_config(display_order) WHERE is_visible;
CREATE INDEX idx_app_monthly_returns_month
    ON public.app_monthly_returns(month_end DESC);
CREATE INDEX idx_app_external_identifiers_fund
    ON public.app_external_identifiers(fund_id);

COMMENT ON TABLE public.app_tigzig_mapping_reviews IS
    'Schemes that deterministic exact-key reconciliation cannot safely attach; fuzzy similarity never writes verified mappings.';
COMMENT ON TABLE public.app_ticker_config IS
    'One verified representative source scheme per canonical fund for ticker read models.';
