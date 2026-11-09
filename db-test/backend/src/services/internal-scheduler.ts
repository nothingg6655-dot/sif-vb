import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import pg from 'pg';
import type { AppConfig } from '../config/env.js';
import { createPool } from '../config/database.js';
import { readFactsheetData } from './pipeline-monitor.js';
import { dueAttempt, retryTime, scheduledSlot, schedulerJobs, type SchedulerJobId } from './scheduler-clock.js';

const worker = fileURLToPath(new URL('../../../scripts/run_server_job.py', import.meta.url));
const projectRoot = fileURLToPath(new URL('../../../', import.meta.url));
const factsheetDirectory = fileURLToPath(new URL('../../../data/factsheets/', import.meta.url));
interface Logger { info: (message: string) => void; error: (message: string) => void }
interface JobRow {
  job_id: SchedulerJobId; enabled: boolean; state: string; scheduled_for: Date | null;
  retry_at: Date | null; attempts: number;
}

export function workerEnvironment(config: AppConfig): NodeJS.ProcessEnv {
  return { ...process.env, PGUSER: config.scheduler.user, PGPASSWORD: config.scheduler.password,
    PGHOST: config.database.host, PGPORT: String(config.database.port),
    PGDATABASE: config.database.database, PGEXPECTED_DATABASE: config.database.database,
    PGSSLMODE: config.database.ssl ? 'verify-full' : 'disable',
    ...(config.database.ssl ? { PGSSLROOTCERT: process.env.PGSSLROOTCERT
      ?? fileURLToPath(new URL('../../certs/supabase-ca.crt', import.meta.url)) } : {}),
    BACKEND_PGUSER: config.scheduler.user, BACKEND_PGPASSWORD: config.scheduler.password,
    FACTSHEET_DATA_DIR: process.env.FACTSHEET_DATA_DIR ?? factsheetDirectory,
    FACTSHEET_MAX_WORKERS: process.env.FACTSHEET_MAX_WORKERS ?? '1',
    NODE_BINARY: process.execPath, PYTHONUNBUFFERED: '1' };
}

export function runWorker(config: AppConfig, job: SchedulerJobId | '--check', signal: AbortSignal, log?: Logger): Promise<number> {
  return new Promise((resolve) => {
    let timedOut = false;
    // A separate process group lets shutdown stop browser/metric child processes too.
    const child = spawn(config.scheduler.python, [worker, job], { cwd: projectRoot,
      env: workerEnvironment(config), stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true, detached: process.platform !== 'win32' });
    const stages: Record<string, string> = { dependencies: 'checking dependencies', nav_source: 'checking NAV source',
      nav_metrics: 'calculating scheme metrics', factsheet_discovery: 'discovering official factsheets',
      factsheet_database: 'ingesting extracted factsheets', complete: 'worker finished' };
    for (const stream of [child.stdout, child.stderr]) {
      let pending = '';
      stream?.on('data', (chunk: Buffer) => {
        const lines = (pending + chunk.toString('utf8')).slice(-8192).split(/\r?\n/);
        pending = lines.pop() ?? '';
        for (const line of lines) {
          const stage = /^SIF_WORKER_STAGE=([a-z_]+)$/.exec(line)?.[1];
          const failure = /^SIF_WORKER_ERROR=([A-Za-z][A-Za-z0-9_]{0,60})$/.exec(line)?.[1];
          // Forward only fixed stage names and exception classes, never raw SQL,
          // URLs, local paths, credentials, or unbounded subprocess output.
          if (stage && stages[stage]) log?.info(`Internal ${job}: ${stages[stage]}.`);
          if (failure) log?.error(`Internal ${job} worker failed (${failure}).`);
        }
      });
    }
    const stop = () => {
      try {
        if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, 'SIGKILL');
        else child.kill('SIGKILL');
      } catch { /* It already exited. */ }
    };
    const timer = setTimeout(() => { timedOut = true; stop(); },
      job === '--check' ? 30000 : job === 'factsheets' ? 40 * 60000 : 90 * 60000);
    const onAbort = () => stop();
    signal.addEventListener('abort', onAbort, { once: true });
    if (signal.aborted) stop();
    const finish = (code: number) => {
      clearTimeout(timer);
      signal.removeEventListener('abort', onAbort);
      resolve(timedOut ? 124 : signal.aborted ? 130 : code);
    };
    child.once('error', () => finish(127));
    child.once('close', (code) => { stop(); finish(code ?? 1); });
  });
}

export class InternalScheduler {
  private pool: pg.Pool;
  private timer?: ReturnType<typeof setInterval>;
  private heartbeatTimer?: ReturnType<typeof setInterval>;
  private running?: Promise<void>;
  private controller = new AbortController();
  private heartbeatRunning = false;

  constructor(private config: AppConfig, private log: Logger,
    private execute = (job: SchedulerJobId, signal: AbortSignal) => runWorker(config, job, signal, log),
    pool?: pg.Pool, private now = () => new Date()) {
    this.pool = pool ?? createPool({ ...config, database: { ...config.database,
      user: config.scheduler.user, password: config.scheduler.password, max: 2,
      application_name: 'sif-internal-scheduler' } });
    this.pool.on('error', () => this.log.error('Scheduler database connection failed; retrying on the next tick.'));
  }

  async initialize() {
    if (await runWorker(this.config, '--check', this.controller.signal, this.log) !== 0) {
      throw new Error('Internal scheduler Python runtime check failed. Install the server worker dependencies.');
    }
    for (const job of schedulerJobs) {
      await this.pool.query(`INSERT INTO public.sif_scheduler_jobs(scope,job_id,next_run_at)
        VALUES ($1,$2,$3) ON CONFLICT (scope,job_id) DO NOTHING`,
      [this.config.scheduler.scope, job, scheduledSlot(job, this.now(), true)]);
    }
  }

  start() {
    const tick = () => {
      if (this.running || this.controller.signal.aborted) return;
      this.running = this.tick().catch(() => {
        this.log.error('Internal scheduler tick failed; durable state will be retried.');
      }).finally(() => { this.running = undefined; });
    };
    tick();
    this.timer = setInterval(tick, 1000);
    this.heartbeatTimer = setInterval(() => { void this.heartbeat(); }, 30000);
    this.log.info('Internal schedulers active: NAV every two hours; factsheets daily at 11:01:59 IST.');
  }

  private async heartbeat() {
    if (this.heartbeatRunning || this.controller.signal.aborted) return;
    this.heartbeatRunning = true;
    try {
      await this.pool.query('UPDATE public.sif_scheduler_jobs SET heartbeat_at=clock_timestamp() WHERE scope=$1',
        [this.config.scheduler.scope]);
    } catch { this.log.error('Internal scheduler heartbeat could not be saved.'); }
    finally { this.heartbeatRunning = false; }
  }

  async tick() {
    const client = await this.pool.connect();
    let locked = false;
    const workerController = new AbortController();
    const lostConnection = () => workerController.abort();
    const shutdown = () => workerController.abort();
    this.controller.signal.addEventListener('abort', shutdown, { once: true });
    client.once('error', lostConnection);
    try {
      const lock = await client.query<{ acquired: boolean }>(
        "SELECT pg_try_advisory_lock(hashtext('sif-internal-scheduler'),hashtext($1)) AS acquired",
        [this.config.scheduler.scope]);
      locked = lock.rows[0]?.acquired === true;
      if (!locked || this.controller.signal.aborted) return;
      const { rows } = await client.query<JobRow>(
        "SELECT * FROM public.sif_scheduler_jobs WHERE scope=$1 ORDER BY CASE job_id WHEN 'nav' THEN 0 ELSE 1 END",
        [this.config.scheduler.scope]);
      // Only one heavy Python worker at a time, including across rolling deploys.
      // Run the oldest due slot first so a slow/retrying NAV import cannot starve
      // the daily factsheet job after a long outage or overlapping execution.
      const tickTime = this.now();
      const candidates = rows.map((row) => ({ row, due: dueAttempt(row.job_id, row, tickTime) }))
        .filter((candidate) => candidate.row.enabled && candidate.due !== null)
        .sort((left, right) => left.due!.slot.getTime() - right.due!.slot.getTime());
      for (const { row, due } of candidates) {
        if (!row.enabled || this.controller.signal.aborted) continue;
        const now = this.now();
        if (!due) continue;
        const token = randomUUID();
        await client.query(`UPDATE public.sif_scheduler_jobs SET state='running',scheduled_for=$3,
          started_at=clock_timestamp(),finished_at=NULL,next_run_at=$4,retry_at=NULL,
          attempts=$5,run_token=$6,last_exit_code=NULL,error_message=NULL,heartbeat_at=clock_timestamp()
          WHERE scope=$1 AND job_id=$2`, [this.config.scheduler.scope, row.job_id, due.slot,
          scheduledSlot(row.job_id, now, true), due.attempt, token]);
        this.log.info(`Internal ${row.job_id} worker started (attempt ${due.attempt}).`);
        let code = 1;
        try { code = await this.execute(row.job_id, workerController.signal); } catch { code = 1; }
        const finished = this.now();
        const retry = code === 0 ? null : retryTime(due.attempt, finished);
        const data = row.job_id === 'factsheets' ? await readFactsheetData(finished,
          workerEnvironment(this.config).FACTSHEET_DATA_DIR) : null;
        const message = code === 0 ? null : code === 124 ? 'The worker exceeded its time limit.'
          : code === 130 ? 'The worker was interrupted by a server restart.'
            : 'The worker failed. The backend will retry within the configured retry limit.';
        await client.query(`UPDATE public.sif_scheduler_jobs SET state=$4,finished_at=$5,
          next_run_at=$6,retry_at=$7,last_exit_code=$8,error_message=$9,
          data_snapshot=CASE WHEN $10::jsonb IS NULL OR $10::jsonb->>'lastCheckedAt' IS NULL
            THEN data_snapshot ELSE $10::jsonb END,heartbeat_at=clock_timestamp()
          WHERE scope=$1 AND job_id=$2 AND run_token=$3`,
        [this.config.scheduler.scope, row.job_id, token,
          code === 0 ? 'succeeded' : code === 130 ? 'interrupted' : 'failed', finished,
          retry && retry < scheduledSlot(row.job_id, finished, true) ? retry : scheduledSlot(row.job_id, finished, true),
          retry, code, message, data ? JSON.stringify(data) : null]);
        this.log.info(`Internal ${row.job_id} worker ${code === 0 ? 'completed' : 'failed'} (exit ${code}).`);
        // Yield to HTTP requests and refresh due slots before running the other worker.
        break;
      }
    } finally {
      client.removeListener('error', lostConnection);
      this.controller.signal.removeEventListener('abort', shutdown);
      try {
        if (locked) await client.query("SELECT pg_advisory_unlock(hashtext('sif-internal-scheduler'),hashtext($1))",
          [this.config.scheduler.scope]);
        client.release();
      } catch { client.release(true); }
    }
  }

  async close() {
    clearInterval(this.timer);
    clearInterval(this.heartbeatTimer);
    this.controller.abort();
    await this.running;
    await this.pool.end();
  }
}
