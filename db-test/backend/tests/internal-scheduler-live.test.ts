import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { parse } from 'dotenv';
import { createPool } from '../src/config/database.js';
import { readConfig } from '../src/config/env.js';
import { InternalScheduler } from '../src/services/internal-scheduler.js';
import { getServerMonitor } from '../src/services/server-monitor.js';
import { scheduledSlot } from '../src/services/scheduler-clock.js';

// Opt-in: creates only a uniquely scoped test job and deletes it as administrator
// afterwards. Never claims or modifies the production 'render' scheduler scope.
test('live scheduler persists success/retries, resumes after restart, and excludes duplicate instances',
  { skip: process.env.SCHEDULER_LIVE_TEST !== 'true' }, async () => {
    const scope = `test-${randomUUID()}`;
    const base = readConfig({ ...process.env, INTERNAL_SCHEDULERS_ENABLED: 'false' });
    const config = readConfig({ ...process.env, INTERNAL_SCHEDULERS_ENABLED: 'true', SCHEDULER_SCOPE: scope,
      SCHEDULER_PGUSER: process.env.PGUSER!, SCHEDULER_PGPASSWORD: process.env.PGPASSWORD! });
    const adminValues = parse(readFileSync(new URL('../../.env.supabase', import.meta.url)));
    const admin = createPool(readConfig({ ...process.env, ...adminValues,
      INTERNAL_SCHEDULERS_ENABLED: 'false', BACKEND_PGUSER: '', BACKEND_PGPASSWORD: '' }));
    const reader = createPool(base);
    const writer = () => createPool({ ...config, database: { ...config.database,
      user: config.scheduler.user, password: config.scheduler.password } });
    const seed = writer();
    let clock = new Date();
    let executions = 0;
    const log = { info() {}, error() {} };
    try {
      await seed.query('INSERT INTO public.sif_scheduler_jobs(scope,job_id,next_run_at) VALUES ($1,$2,$3)',
        [scope, 'nav', scheduledSlot('nav', clock, true)]);
      const first = new InternalScheduler(config, log, async () => { executions++; return 0; }, writer(), () => clock);
      try { await first.tick(); } finally { await first.close(); }
      const saved = (await reader.query('SELECT * FROM public.sif_scheduler_jobs WHERE scope=$1', [scope])).rows[0]!;
      assert.equal(saved.state, 'succeeded');
      assert.equal(saved.last_exit_code, 0);
      assert.equal(saved.attempts, 1);
      const status = await getServerMonitor(reader, 1440, scope);
      assert.equal(status.jobs[1]?.state, 'succeeded');
      assert.equal(status.jobs[1]?.data.metrics['Runs on'], 'Render backend');
      await assert.rejects(reader.query("UPDATE public.sif_scheduler_jobs SET state='failed' WHERE scope=$1", [scope]),
        (error: unknown) => (error as { code?: string }).code === '42501');

      const resumed = new InternalScheduler(config, log, async () => { executions++; return 0; }, writer(), () => clock);
      try { await resumed.tick(); } finally { await resumed.close(); }
      assert.equal(executions, 1, 'completed slots survive restarts without rerunning');

      clock = scheduledSlot('nav', clock, true);
      const failed = new InternalScheduler(config, log, async () => { executions++; return 1; }, writer(), () => clock);
      try { await failed.tick(); } finally { await failed.close(); }
      const retry = (await reader.query('SELECT * FROM public.sif_scheduler_jobs WHERE scope=$1', [scope])).rows[0]!;
      assert.equal(retry.state, 'failed');
      assert.equal(retry.retry_at.getTime(), clock.getTime() + 5 * 60000);

      const owner = await seed.connect();
      try {
        await owner.query("SELECT pg_advisory_lock(hashtext('sif-internal-scheduler'),hashtext($1))", [scope]);
        const competing = new InternalScheduler(config, log, async () => { executions++; return 0; }, writer(), () => clock);
        try { await competing.tick(); } finally { await competing.close(); }
        assert.equal(executions, 2, 'another instance cannot execute while the global lock is held');
      } finally {
        await owner.query("SELECT pg_advisory_unlock(hashtext('sif-internal-scheduler'),hashtext($1))", [scope]);
        owner.release();
      }
      clock = new Date(clock.getTime() + 5 * 60000);
      const retried = new InternalScheduler(config, log, async () => { executions++; return 0; }, writer(), () => clock);
      try { await retried.tick(); } finally { await retried.close(); }
      const recovered = (await reader.query('SELECT * FROM public.sif_scheduler_jobs WHERE scope=$1', [scope])).rows[0]!;
      assert.equal(recovered.state, 'succeeded');
      assert.equal(recovered.attempts, 2);
      assert.equal(recovered.retry_at, null);
    } finally {
      await admin.query('DELETE FROM public.sif_scheduler_jobs WHERE scope=$1', [scope]);
      await Promise.all([seed.end(), reader.end(), admin.end()]);
    }
  });
