import type pg from 'pg';

export interface SyncRunRow {
  runId: string;
  triggerType: string;
  jobType: string;
  jobIdentifier: string | null;
  startedAt: Date;
  completedAt: Date | null;
  status: string;
  manifestGeneratedAt: Date | null;
  recordsRead: number;
  insertedCount: number;
  updatedCount: number;
  skippedCount: number;
  rejectedCount: number;
  errorMessage: string | null;
}

export interface DatasetStatusRow {
  datasetName: string;
  filename: string;
  manifestGeneratedAt: Date | null;
  lastCheckedAt: Date;
  lastSuccessfulImportAt: Date | null;
  manifestRowCount: number | null;
  databaseRowCount: number;
  sha256: string | null;
}

const runColumns = `
  run_id::text AS "runId", trigger_type AS "triggerType", job_type AS "jobType",
  manifest_metadata #>> '{_vb_sif,job_identifier}' AS "jobIdentifier",
  started_at AS "startedAt", completed_at AS "completedAt", status,
  manifest_generated_at AS "manifestGeneratedAt",
  records_read::integer AS "recordsRead", inserted_count::integer AS "insertedCount",
  updated_count::integer AS "updatedCount", skipped_count::integer AS "skippedCount",
  rejected_count::integer AS "rejectedCount", error_message AS "errorMessage"`;

export async function readSyncStatus(pool: pg.Pool) {
  const [attempted, successful, failed, datasets] = await Promise.all([
    pool.query<SyncRunRow>(`SELECT ${runColumns} FROM public.tigzig_sync_runs ORDER BY started_at DESC LIMIT 1`),
    pool.query<SyncRunRow>(`SELECT ${runColumns} FROM public.tigzig_sync_runs WHERE status='succeeded' AND job_type IN ('backfill','sync','revalidate') ORDER BY completed_at DESC LIMIT 1`),
    pool.query<SyncRunRow>(`SELECT ${runColumns} FROM public.tigzig_sync_runs WHERE status IN ('failed','interrupted') ORDER BY started_at DESC LIMIT 10`),
    pool.query<DatasetStatusRow>(`
      SELECT v.dataset_name AS "datasetName", v.filename,
             v.manifest_generated_at AS "manifestGeneratedAt",
             v.last_checked_at AS "lastCheckedAt",
             v.last_successful_import_at AS "lastSuccessfulImportAt",
             v.row_count::integer AS "manifestRowCount",
             CASE v.dataset_name
               WHEN 'meta' THEN (SELECT count(*)::integer FROM public.tigzig_schemes)
               WHEN 'nav' THEN (SELECT count(*)::integer FROM public.tigzig_scheme_nav)
               WHEN 'aaum' THEN (SELECT count(*)::integer FROM public.tigzig_scheme_aaum)
             END AS "databaseRowCount", v.sha256
      FROM public.tigzig_sync_file_versions v ORDER BY v.dataset_name`)
  ]);
  return {
    lastAttemptedSync: attempted.rows[0] ?? null,
    lastSuccessfulSync: successful.rows[0] ?? null,
    failedRuns: failed.rows,
    datasets: datasets.rows
  };
}
