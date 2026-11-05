-- Verified SIF360-compatible fund identifiers and Regular Growth NAV selections.

CREATE TABLE public.app_performance_config (
    reference_fund_id INTEGER PRIMARY KEY CHECK (reference_fund_id BETWEEN 1 AND 34),
    fund_id BIGINT NOT NULL UNIQUE REFERENCES public.app_funds(fund_id),
    preferred_source TEXT NOT NULL,
    preferred_scheme_code BIGINT NOT NULL,
    mapping_confidence TEXT NOT NULL
        CHECK (mapping_confidence IN ('high', 'medium', 'low', 'unresolved')),
    selection_verified BOOLEAN NOT NULL DEFAULT FALSE,
    selection_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(selection_evidence) = 'object'),
    verified_by TEXT,
    verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (preferred_source, preferred_scheme_code),
    FOREIGN KEY (fund_id, preferred_source, preferred_scheme_code)
        REFERENCES public.app_tigzig_fund_mapping(fund_id, source, scheme_code),
    CHECK ((selection_verified AND verified_at IS NOT NULL
            AND btrim(verified_by) <> '' AND selection_evidence <> '{}'::jsonb)
           OR (NOT selection_verified AND verified_at IS NULL AND verified_by IS NULL))
);

CREATE INDEX idx_app_performance_verified_reference
    ON public.app_performance_config(reference_fund_id)
    WHERE selection_verified;

COMMENT ON TABLE public.app_performance_config IS
    'Verified mapping from the external 1-34 performance response identifiers to one Regular Growth NAV series per canonical fund.';
