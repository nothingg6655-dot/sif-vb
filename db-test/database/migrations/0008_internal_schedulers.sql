-- Durable state for jobs executed inside the existing backend service.
CREATE TABLE public.sif_scheduler_jobs (
  scope TEXT NOT NULL CHECK (length(scope) BETWEEN 1 AND 80),
  job_id TEXT NOT NULL CHECK (job_id IN ('factsheets','nav')),
  enabled BOOLEAN NOT NULL DEFAULT true,
  state TEXT NOT NULL DEFAULT 'not_run'
    CHECK (state IN ('not_run','running','succeeded','failed','interrupted')),
  scheduled_for TIMESTAMPTZ,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  next_run_at TIMESTAMPTZ NOT NULL,
  retry_at TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 3),
  run_token UUID,
  last_exit_code INTEGER,
  error_message TEXT CHECK (length(error_message) <= 500),
  heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  data_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(data_snapshot)='object'),
  PRIMARY KEY (scope,job_id)
);
ALTER TABLE public.sif_scheduler_jobs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.sif_scheduler_jobs FROM PUBLIC;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
    REVOKE ALL ON public.sif_scheduler_jobs FROM anon, authenticated;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='sif_backend') THEN
    GRANT SELECT ON public.sif_scheduler_jobs TO sif_backend;
    CREATE POLICY scheduler_backend_read ON public.sif_scheduler_jobs FOR SELECT TO sif_backend USING (true);
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='sif_ingestion') THEN
    GRANT SELECT,INSERT,UPDATE ON public.sif_scheduler_jobs TO sif_ingestion;
    CREATE POLICY scheduler_worker_read ON public.sif_scheduler_jobs FOR SELECT TO sif_ingestion USING (true);
    CREATE POLICY scheduler_worker_insert ON public.sif_scheduler_jobs FOR INSERT TO sif_ingestion WITH CHECK (true);
    CREATE POLICY scheduler_worker_update ON public.sif_scheduler_jobs FOR UPDATE TO sif_ingestion USING (true) WITH CHECK (true);
  END IF;
END $$;
