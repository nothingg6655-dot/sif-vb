-- Reuse the existing historical return/risk caches. The authoritative source
-- identity is (source, scheme_code), independent of optional strategy mappings.
ALTER TABLE public.sif_scheme_return_cache ADD COLUMN source TEXT;
ALTER TABLE public.sif_scheme_risk_cache ADD COLUMN source TEXT;
UPDATE public.sif_scheme_return_cache c SET source = s.source
FROM public.sif_schemes s WHERE s.scheme_code = c.scheme_code;
UPDATE public.sif_scheme_risk_cache c SET source = s.source
FROM public.sif_schemes s WHERE s.scheme_code = c.scheme_code;

ALTER TABLE public.sif_scheme_return_cache
  ALTER COLUMN source SET NOT NULL,
  DROP CONSTRAINT sif_scheme_return_cache_pkey,
  DROP CONSTRAINT sif_scheme_return_cache_scheme_code_fkey,
  ADD PRIMARY KEY (source,scheme_code,as_of_date,horizon,method,calculation_version),
  ADD FOREIGN KEY (source,scheme_code) REFERENCES public.tigzig_schemes(source,scheme_code),
  ALTER COLUMN start_nav TYPE NUMERIC(24,10),
  ALTER COLUMN end_nav TYPE NUMERIC(24,10),
  ALTER COLUMN return_pct TYPE NUMERIC(38,18),
  ADD COLUMN target_date DATE,
  ADD COLUMN calculation_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(calculation_metadata) = 'object'),
  ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE public.sif_scheme_risk_cache ADD COLUMN horizon VARCHAR(30);
UPDATE public.sif_scheme_risk_cache SET horizon = 'D' || lookback_days::text;
ALTER TABLE public.sif_scheme_risk_cache DROP CONSTRAINT sif_scheme_risk_cache_pkey;
ALTER TABLE public.sif_scheme_risk_cache
  ALTER COLUMN source SET NOT NULL,
  ALTER COLUMN horizon SET NOT NULL,
  ALTER COLUMN lookback_days DROP NOT NULL,
  DROP CONSTRAINT sif_scheme_risk_cache_scheme_code_fkey,
  ADD PRIMARY KEY (source,scheme_code,as_of_date,horizon,calculation_version),
  ADD FOREIGN KEY (source,scheme_code) REFERENCES public.tigzig_schemes(source,scheme_code),
  ALTER COLUMN annualized_volatility_pct TYPE NUMERIC(38,18),
  ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX idx_sif_return_scheme_date ON public.sif_scheme_return_cache(source,scheme_code,as_of_date DESC);
CREATE INDEX idx_sif_risk_date_horizon ON public.sif_scheme_risk_cache(as_of_date,horizon);
ALTER TABLE public.sif_scheme_return_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sif_scheme_risk_cache ENABLE ROW LEVEL SECURITY;
-- The API may calculate derived metrics, but remains unable to mutate source NAVs.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sif_backend') THEN
    GRANT SELECT,INSERT,UPDATE ON public.sif_scheme_return_cache,public.sif_scheme_risk_cache TO sif_backend;
    CREATE POLICY sif_backend_metrics_insert ON public.sif_scheme_return_cache FOR INSERT TO sif_backend WITH CHECK (true);
    CREATE POLICY sif_backend_metrics_update ON public.sif_scheme_return_cache FOR UPDATE TO sif_backend USING (true) WITH CHECK (true);
    CREATE POLICY sif_backend_metrics_insert ON public.sif_scheme_risk_cache FOR INSERT TO sif_backend WITH CHECK (true);
    CREATE POLICY sif_backend_metrics_update ON public.sif_scheme_risk_cache FOR UPDATE TO sif_backend USING (true) WITH CHECK (true);
  END IF;
END $$;
REVOKE ALL ON public.sif_scheme_return_cache,public.sif_scheme_risk_cache FROM PUBLIC;
