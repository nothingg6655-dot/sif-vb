import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import type pg from 'pg';
import { z } from 'zod';
import { readSyncStatus } from '../repositories/sync-status.js';

const runFile = promisify(execFile);
const taskNames = ['SIF Factsheet Monitor', 'SIF NAV Sync'] as const;
const timestamp = z.string().refine((value) => Number.isFinite(Date.parse(value)));
const taskSchema = z.object({
  taskName: z.enum(taskNames), readStatus: z.enum(['ok', 'missing', 'unavailable']),
  enabled: z.boolean().nullable(), state: z.string(), lastRunAt: timestamp.nullable(),
  nextRunAt: timestamp.nullable(), lastExitCode: z.number().int().nonnegative().nullable(),
  triggerTimes: z.array(z.string().regex(/^\d{2}:\d{2}:\d{2}$/)),
});
export type TaskSnapshot = z.infer<typeof taskSchema>;
export type Freshness = 'fresh' | 'stale' | 'unknown';
export interface CheckIssue { name: string; message: string }
export interface MonitorData {
  lastCheckedAt: string | null; freshness: Freshness; summary: string;
  metrics: Record<string, string | number | null>; issues: CheckIssue[];
}
export interface JobMonitor {
  id: string; name: string;
  state: 'running' | 'queued' | 'succeeded' | 'failed' | 'disabled' | 'not_run' | 'missing' | 'unavailable';
  schedule: string; lastRunAt: string | null; nextRunAt: string | null;
  lastExitCode: number | null; taskError: string | null; data: MonitorData;
}
let taskCache: { until: number; value: Promise<TaskSnapshot[]> } | undefined;

function unavailableTask(taskName: typeof taskNames[number]): TaskSnapshot {
  return { taskName, readStatus: 'unavailable', enabled: null, state: 'Unknown',
    lastRunAt: null, nextRunAt: null, lastExitCode: null, triggerTimes: [] };
}

export async function readScheduledTasks(): Promise<TaskSnapshot[]> {
  if (taskCache && taskCache.until > Date.now()) return taskCache.value;
  const value = (async () => {
    if (process.platform !== 'win32') return taskNames.map(unavailableTask);
    try {
      const script = fileURLToPath(new URL('../../scripts/read-scheduler-status.ps1', import.meta.url));
      const { stdout } = await runFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-File', script],
        { windowsHide: true, timeout: 10000, maxBuffer: 65536, encoding: 'utf8' });
      const result = z.array(taskSchema).parse(JSON.parse(stdout.replace(/^\uFEFF/, '')));
      return taskNames.map((name) => result.find((task) => task.taskName === name) ?? unavailableTask(name));
    } catch { return taskNames.map(unavailableTask); }
  })();
  taskCache = { until: Date.now() + 15000, value };
  return value;
}

export function checkFreshness(value: string | null, thresholdMinutes: number, now = new Date()): Freshness {
  if (!value || !Number.isFinite(Date.parse(value))) return 'unknown';
  const age = now.getTime() - Date.parse(value);
  if (age < -5 * 60000) return 'unknown';
  return age <= thresholdMinutes * 60000 ? 'fresh' : 'stale';
}

export function summarizeTask(task: TaskSnapshot, id: string, name: string, data: MonitorData): JobMonitor {
  let state: JobMonitor['state'] = 'unavailable';
  if (task.readStatus === 'missing') state = 'missing';
  else if (task.readStatus === 'ok') {
    if (!task.enabled || task.state === 'Disabled') state = 'disabled';
    else if (task.state === 'Running') state = 'running';
    else if (task.state === 'Queued') state = 'queued';
    else if (!task.lastRunAt || task.lastExitCode === 0x41303) state = 'not_run';
    else state = task.lastExitCode === 0 ? 'succeeded' : 'failed';
  }
  const times = task.triggerTimes;
  const everyTwoHours = times.length === 12 && times.every((time, i) => time === `${String(i * 2).padStart(2, '0')}:00:00`);
  const schedule = task.readStatus !== 'ok' ? 'Unavailable'
    : everyTwoHours ? 'Every 2 hours (IST)'
      : times.length ? `Daily at ${times.map((time) => time.endsWith(':00') ? time.slice(0, 5) : time).join(', ')} IST`
        : 'No timed trigger';
  return {
    id, name, state, schedule, lastRunAt: task.lastRunAt, nextRunAt: task.nextRunAt,
    lastExitCode: task.lastExitCode,
    taskError: state === 'failed' ? task.lastExitCode === 2147942667
      ? 'The scheduled task working directory is invalid.' : `The task reported exit code ${task.lastExitCode}.`
      : state === 'missing' ? 'This task is not registered on the backend computer.'
        : state === 'unavailable' ? 'The backend could not verify this Windows task.' : null,
    data,
  };
}

function unknownData(summary: string): MonitorData {
  return { lastCheckedAt: null, freshness: 'unknown', summary, metrics: {}, issues: [] };
}

async function boundedCheck<T>(check: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([check, new Promise<never>((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error('Monitor check timed out')), 12000);
    })]);
  } finally { clearTimeout(timer); }
}

function publisherIssue(errors: string[]): string {
  const message = errors.join(' ').toLowerCase();
  if (/403|forbidden|access.denied/.test(message)) return 'Publisher access was denied.';
  if (/reject/.test(message)) return 'The publisher rejected the request.';
  if (/timed? ?out|timeout/.test(message)) return 'The publisher request timed out.';
  if (/adapter|layout/.test(message)) return 'The factsheet format needs extraction support.';
  if (/validat|subtotal|reconcil/.test(message)) return 'Factsheet validation needs review.';
  return 'The latest publisher check needs attention.';
}

const coverageSchema = z.object({
  'Generated At': timestamp, 'Registered Schemes': z.number().int().nonnegative(),
  'Extracted Schemes': z.number().int().nonnegative(),
  Schemes: z.array(z.object({ Scheme: z.string(), Status: z.string(),
    'Reporting Month': z.string().nullable().optional(), 'Latest Listing Verified': z.boolean().optional() })),
});
const sourceStateSchema = z.object({ sources: z.record(z.string(), z.object({
  fund_house: z.string(), status: z.string(), errors: z.array(z.string()).optional(),
  failed_funds: z.record(z.string(), z.string()).optional(),
})) });
const manifestSchema = z.object({ 'PDF Count': z.number().int().nonnegative() });

export async function readFactsheetData(now = new Date()): Promise<MonitorData> {
  try {
    const base = new URL('../../../../SIF/output/', import.meta.url);
    // src/services and dist/services have identical depth relative to Backennd.
    const [coverageRaw, stateRaw, manifestRaw] = await Promise.all([
      readFile(new URL('latest/coverage.json', base), 'utf8'),
      readFile(new URL('monitor_state.json', base), 'utf8'),
      readFile(new URL('latest/manifest.json', base), 'utf8'),
    ]);
    const coverage = coverageSchema.parse(JSON.parse(coverageRaw));
    const state = sourceStateSchema.parse(JSON.parse(stateRaw));
    const manifest = manifestSchema.parse(JSON.parse(manifestRaw));
    const issues = Object.values(state.sources).filter((source) => source.status === 'error')
      .map((source) => ({ name: source.fund_house,
        message: publisherIssue([...(source.errors ?? []), ...Object.values(source.failed_funds ?? {})]) }));
    const months = coverage.Schemes.map((scheme) => scheme['Reporting Month']).filter((month): month is string => !!month).sort();
    const extracted = coverage['Extracted Schemes'], registered = coverage['Registered Schemes'];
    return {
      lastCheckedAt: coverage['Generated At'], freshness: checkFreshness(coverage['Generated At'], 26 * 60, now),
      summary: `${extracted} of ${registered} schemes extracted${issues.length ? `; ${issues.length} publisher checks need attention` : ''}.`,
      metrics: { 'Extracted schemes': `${extracted} / ${registered}`, 'Current PDFs': manifest['PDF Count'],
        'Latest listing verified': coverage.Schemes.filter((scheme) => scheme['Latest Listing Verified'] === true).length,
        'Latest reporting month': months.at(-1) ?? null,
        'Schemes awaiting extraction': coverage.Schemes.filter((scheme) => scheme.Status !== 'extracted').length },
      issues,
    };
  } catch { return unknownData('Factsheet monitor output is unavailable on this backend computer.'); }
}

export async function readNavMonitorData(pool: pg.Pool, freshnessMaxMinutes: number, now = new Date()) {
  const [status, latest] = await Promise.all([
    readSyncStatus(pool),
    pool.query<{ latestDate: string | null }>('SELECT max(nav_date)::text AS "latestDate" FROM public.tigzig_scheme_nav'),
  ]);
  const attempt = status.lastAttemptedSync;
  const sourceTimes = status.datasets.map((dataset) => dataset.manifestGeneratedAt?.toISOString()).filter((time): time is string => !!time).sort();
  const manifestAt = sourceTimes.at(-1) ?? null;
  const checkedAt = (attempt?.completedAt ?? attempt?.startedAt)?.toISOString() ?? null;
  const complete = attempt && attempt.status !== 'running';
  const issues: CheckIssue[] = [];
  if (attempt?.status === 'failed' || attempt?.status === 'interrupted') {
    issues.push({ name: 'NAV source check', message: 'The latest source check did not complete successfully.' });
  }
  if (checkFreshness(manifestAt, freshnessMaxMinutes, now) === 'stale') {
    issues.push({ name: 'Source freshness', message: 'The provider manifest is older than the configured freshness limit.' });
  }
  return {
    lastCheckedAt: checkedAt, freshness: checkFreshness(checkedAt, 3 * 60, now),
    summary: !attempt ? 'No NAV check has been recorded.' : attempt.status === 'running' ? 'Checking NAV source data.'
      : attempt.status !== 'succeeded' ? 'The latest NAV source check needs attention.'
        : attempt.sourceUnchanged ? 'No source changes; no NAV data was rewritten.'
          : `Latest source check: ${attempt.insertedCount} inserted, ${attempt.updatedCount} updated.`,
    metrics: { 'Latest NAV date': latest.rows[0]?.latestDate ?? null,
      'Stored NAV observations': status.datasets.find((dataset) => dataset.datasetName === 'nav')?.databaseRowCount ?? null,
      'Source manifest': manifestAt, 'Last batch inserted': complete ? attempt.insertedCount : null,
      'Last batch updated': complete ? attempt.updatedCount : null },
    issues,
  } satisfies MonitorData;
}

export async function getPipelineMonitor(pool: pg.Pool, freshnessMaxMinutes: number, dependencies: {
  tasks?: () => Promise<TaskSnapshot[]>; factsheets?: () => Promise<MonitorData>;
  nav?: () => Promise<MonitorData>;
} = {}) {
  const now = new Date();
  const [tasksResult, factsheetsResult, navResult] = await Promise.allSettled([
    (dependencies.tasks ?? readScheduledTasks)(), (dependencies.factsheets ?? readFactsheetData)(),
    boundedCheck(dependencies.nav ? dependencies.nav() : readNavMonitorData(pool, freshnessMaxMinutes, now)),
  ]);
  const tasks = tasksResult.status === 'fulfilled' ? tasksResult.value : taskNames.map(unavailableTask);
  const factsheets = factsheetsResult.status === 'fulfilled' ? factsheetsResult.value : unknownData('Factsheet check data is unavailable.');
  const nav = navResult.status === 'fulfilled' ? navResult.value : unknownData('NAV check data is unavailable because the database check failed.');
  return {
    checkedAt: new Date().toISOString(), timezone: 'Asia/Kolkata',
    backend: { status: 'online', database: navResult.status === 'fulfilled' ? 'connected' : 'unavailable' },
    jobs: [summarizeTask(tasks.find((task) => task.taskName === taskNames[0]) ?? unavailableTask(taskNames[0]), 'factsheets', 'Factsheet Monitor', factsheets),
      summarizeTask(tasks.find((task) => task.taskName === taskNames[1]) ?? unavailableTask(taskNames[1]), 'nav', 'NAV Scheduler', nav)],
  };
}
