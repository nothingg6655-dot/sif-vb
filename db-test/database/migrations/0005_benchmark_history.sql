-- Benchmark-ready storage. Definitions are configured without inventing observations.

CREATE TABLE public.app_benchmarks (
    benchmark_id TEXT PRIMARY KEY CHECK (benchmark_id ~ '^[a-z0-9]+$'),
    label TEXT NOT NULL CHECK (btrim(label) <> ''),
    subtitle TEXT NOT NULL CHECK (btrim(subtitle) <> ''),
    index_variant TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (index_variant IN ('PRICE', 'TRI', 'UNKNOWN')),
    currency CHAR(3) NOT NULL DEFAULT 'INR',
    source TEXT,
    source_identifier TEXT,
    coverage_status TEXT NOT NULL DEFAULT 'unavailable'
        CHECK (coverage_status IN ('available', 'partial', 'unavailable')),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(metadata) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((coverage_status = 'unavailable') OR
           (btrim(source) <> '' AND btrim(source_identifier) <> '' AND index_variant <> 'UNKNOWN'))
);

CREATE TABLE public.app_benchmark_history (
    benchmark_id TEXT NOT NULL REFERENCES public.app_benchmarks(benchmark_id),
    observation_date DATE NOT NULL,
    index_level NUMERIC(28,10) NOT NULL
        CHECK (index_level > 0 AND index_level <> 'NaN'::numeric),
    source_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (benchmark_id, observation_date)
);

CREATE INDEX idx_app_benchmark_history_date
    ON public.app_benchmark_history(observation_date DESC);

INSERT INTO public.app_benchmarks(benchmark_id,label,subtitle,index_variant,coverage_status,metadata)
VALUES
  ('bse500','BSE 500','Broad Market Index','UNKNOWN','unavailable',
   '{"reason":"No verified historical index-level source is configured"}'::jsonb),
  ('nifty50','NIFTY 50','Broad Market Index','UNKNOWN','unavailable',
   '{"reason":"No verified historical index-level source is configured"}'::jsonb),
  ('niftymidsml','NIFTY MidSmallcap 400','Mid & Small Cap Index','UNKNOWN','unavailable',
   '{"reason":"No verified historical index-level source is configured"}'::jsonb);

COMMENT ON TABLE public.app_benchmark_history IS
    'Immutable-source benchmark index levels. PRICE versus TRI must be declared on app_benchmarks before rows are treated as available.';
