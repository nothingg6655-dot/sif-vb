import type pg from 'pg';
import { z } from 'zod';
import { boundedCheck, checkFreshness, readNavMonitorData, type JobMonitor, type MonitorData } from './pipeline-monitor.js';

const dataSchema = z.object({
  lastCheckedAt: z.string().nullable(), freshness: z.enum(['fresh','stale','unknown']), summary: z.string(),
  metrics: z.record(z.string(), z.union([z.string(), z.number(), z.null()])),
  issues: z.array(z.object({ name: z.string(), message: z.string() })),
});
interface ServerJobRow {
  job_id: string; enabled: boolean; state: string; started_at: Date | null;
  next_run_at: Date; last_exit_code: number | null; error_message: string | null;
  heartbeat_at: Date; data_snapshot: unknown;
}
function noData(message: string): MonitorData {
  return { lastCheckedAt: null, freshness: 'unknown', summary: message, metrics: {}, issues: [] };
}

export function serverJob(row: ServerJobRow | undefined, id: string, now = new Date()): JobMonitor {
  const snapshot = dataSchema.safeParse(row?.data_snapshot);
  const data = snapshot.success ? snapshot.data : noData('This server job has not completed a data check yet.');
  data.freshness = checkFreshness(data.lastCheckedAt, id === 'factsheets' ? 26 * 60 : 180, now);
  const heartbeat = row?.heartbeat_at.toISOString() ?? null;
  const awake = heartbeat !== null && checkFreshness(heartbeat, 2, now) === 'fresh';
  data.metrics = { ...data.metrics, 'Runs on': 'Render backend', 'Scheduler heartbeat': heartbeat };
  const pending = row && row.next_run_at.getTime() < now.getTime() - 2000
    && ['succeeded', 'not_run'].includes(row.state);
  const state: JobMonitor['state'] = !row ? 'unavailable' : !row.enabled ? 'disabled'
    : !awake ? 'unavailable' : row.state === 'interrupted' ? 'failed'
      : pending ? 'queued'
      : ['running','succeeded','failed','not_run'].includes(row.state) ? row.state as JobMonitor['state'] : 'unavailable';
  const error = !row ? 'The internal scheduler has not registered this job.'
    : !awake ? 'The server scheduler heartbeat is overdue. The backend may be restarting or asleep.'
      : row.error_message;
  if (!awake) data.issues.push({ name: 'Scheduler heartbeat', message: error! });
  return { id, name: id === 'factsheets' ? 'Factsheet Monitor' : 'NAV Scheduler', state,
    schedule: id === 'factsheets' ? 'Daily at 11:01:59 IST' : 'Every 2 hours (IST)',
    lastRunAt: row?.started_at?.toISOString() ?? null, nextRunAt: row?.next_run_at.toISOString() ?? null,
    lastExitCode: row?.last_exit_code ?? null, taskError: error ?? null, data };
}

export async function getServerMonitor(pool: pg.Pool, freshnessMaxMinutes: number, scope = 'render') {
  const [jobsResult, navResult] = await Promise.allSettled([
    boundedCheck(pool.query<ServerJobRow>('SELECT * FROM public.sif_scheduler_jobs WHERE scope=$1 ORDER BY job_id', [scope])),
    boundedCheck(readNavMonitorData(pool, freshnessMaxMinutes)),
  ]);
  const rows = jobsResult.status === 'fulfilled' ? jobsResult.value.rows : [];
  const now = new Date();
  const jobs = ['factsheets','nav'].map((id) => serverJob(rows.find((row) => row.job_id === id), id, now));
  if (jobsResult.status === 'rejected') {
    for (const job of jobs) job.taskError = 'Scheduler status could not be read from the database.';
  }
  const navJob = jobs[1]!;
  const hostMetrics = navJob.data.metrics;
  const heartbeatIssues = navJob.data.issues;
  navJob.data = navResult.status === 'fulfilled' ? navResult.value
    : noData('NAV database check is unavailable.');
  navJob.data.metrics = { ...navJob.data.metrics, ...hostMetrics };
  navJob.data.issues.push(...heartbeatIssues);
  return { checkedAt: now.toISOString(), timezone: 'Asia/Kolkata',
    backend: { status: 'online', database: jobsResult.status === 'fulfilled' && navResult.status === 'fulfilled'
      ? 'connected' : 'unavailable' }, jobs };
}
