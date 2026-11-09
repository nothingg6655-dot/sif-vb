import assert from 'node:assert/strict';
import { test } from 'node:test';
import { EventEmitter } from 'node:events';
import type pg from 'pg';
import { readConfig } from '../src/config/env.js';
import { dueAttempt, retryTime, scheduledSlot } from '../src/services/scheduler-clock.js';
import { serverJob, getServerMonitor } from '../src/services/server-monitor.js';
import { InternalScheduler, workerEnvironment } from '../src/services/internal-scheduler.js';

const config = readConfig({ PGHOST: 'localhost', PGDATABASE: 'VB-SIF', PGUSER: 'reader', PGPASSWORD: 'reader-test',
  INTERNAL_SCHEDULERS_ENABLED: 'true', SCHEDULER_PGUSER: 'writer', SCHEDULER_PGPASSWORD: 'writer-test' });
const log = { info() {}, error() {} };

test('server clock preserves the precise IST schedules across midnight and UTC day boundaries', () => {
  for (const [job, input, latest, next] of [
    ['factsheets','2026-11-09T05:31:58Z','2026-11-08T05:31:59Z','2026-11-09T05:31:59Z'],
    ['factsheets','2026-11-09T05:31:59Z','2026-11-09T05:31:59Z','2026-11-10T05:31:59Z'],
    ['nav','2026-11-09T06:30:00Z','2026-11-09T06:30:00Z','2026-11-09T08:30:00Z'],
    ['nav','2026-11-09T18:29:59Z','2026-11-09T16:30:00Z','2026-11-09T18:30:00Z'],
    ['nav','2026-11-09T18:30:00Z','2026-11-09T18:30:00Z','2026-11-09T20:30:00Z'],
  ] as const) {
    assert.equal(scheduledSlot(job, new Date(input)).toISOString(), new Date(latest).toISOString());
    assert.equal(scheduledSlot(job, new Date(input), true).toISOString(), new Date(next).toISOString());
  }
});

test('restart recovery coalesces missed slots and successful slots never rerun', () => {
  const now = new Date('2026-11-09T07:00:00Z');
  const row = { state: 'succeeded', scheduled_for: new Date('2026-11-08T06:30:00Z'), retry_at: null, attempts: 1 };
  const recovered = dueAttempt('nav', row, now)!;
  assert.equal(recovered.slot.toISOString(), '2026-11-09T06:30:00.000Z');
  assert.equal(recovered.attempt, 1);
  assert.equal(dueAttempt('nav', { ...row, scheduled_for: recovered.slot }, now), null);
  assert.equal(dueAttempt('nav', { ...row, state: 'running', scheduled_for: recovered.slot }, now)?.attempt, 2);
});

test('failures retry with backoff, stop after three attempts, and resume on the next normal slot', () => {
  const now = new Date('2026-11-09T07:00:00Z');
  assert.equal(retryTime(1, now)?.toISOString(), '2026-11-09T07:05:00.000Z');
  assert.equal(retryTime(2, now)?.toISOString(), '2026-11-09T07:10:00.000Z');
  assert.equal(retryTime(3, now), null);
  const row = { state: 'failed', scheduled_for: new Date('2026-11-09T06:30:00Z'), retry_at: retryTime(1, now), attempts: 1 };
  assert.equal(dueAttempt('nav', row, now), null);
  assert.equal(dueAttempt('nav', row, new Date('2026-11-09T07:05:00Z'))?.attempt, 2);
  assert.equal(dueAttempt('nav', { ...row, attempts: 3 }, new Date('2026-11-09T08:00:00Z')), null);
  assert.equal(dueAttempt('nav', { ...row, attempts: 3 }, new Date('2026-11-09T08:30:00Z'))?.attempt, 1);
});

const now = new Date('2026-11-09T07:00:00Z');
const row = { job_id: 'factsheets', enabled: true, state: 'succeeded', started_at: new Date('2026-11-09T05:31:59Z'),
  next_run_at: new Date('2026-11-10T05:31:59Z'), last_exit_code: 0, error_message: null, heartbeat_at: now,
  data_snapshot: { lastCheckedAt: '2026-11-09T05:32:50Z', freshness: 'fresh', summary: '28 of 34 schemes extracted.',
    metrics: { 'Extracted schemes': '28 / 34' }, issues: [{ name: 'Publisher', message: 'Publisher access was denied.' }] } };

test('UI status uses durable server results and ages heartbeat and factsheet checks independently', () => {
  const current = serverJob(row, 'factsheets', now);
  assert.equal(current.state, 'succeeded');
  assert.equal(current.data.metrics['Extracted schemes'], '28 / 34');
  assert.equal(current.schedule, 'Daily at 11:01:59 IST');
  const asleep = serverJob(row, 'factsheets', new Date('2026-11-09T07:03:00Z'));
  assert.equal(asleep.state, 'unavailable');
  assert.match(asleep.taskError!, /heartbeat is overdue/);
  assert.equal(asleep.data.freshness, 'fresh');
  const stale = serverJob({ ...row, heartbeat_at: new Date('2026-11-11T07:00:00Z') }, 'factsheets', new Date('2026-11-11T07:00:00Z'));
  assert.equal(stale.data.freshness, 'stale');
});

test('production monitor works without Windows or local monitor files', async () => {
  const freshRow = { ...row, heartbeat_at: new Date(), next_run_at: new Date(Date.now() + 86400000) };
  const pool = { query: async (sql: string) => ({ rows: sql.includes('sif_scheduler_jobs') ? [freshRow]
    : sql.includes('max(nav_date)') ? [{ latestDate: '2026-11-06' }] : [] }) } as unknown as pg.Pool;
  const result = await getServerMonitor(pool, 1440);
  assert.equal(result.backend.database, 'connected');
  assert.equal(result.jobs[0]?.state, 'succeeded');
  assert.equal(result.jobs[0]?.data.metrics['Runs on'], 'Render backend');
  assert.ok(!JSON.stringify(result).includes('Windows'));
});

test('internal scheduler refuses missing writer credentials and child workers use the dedicated role', () => {
  assert.throws(() => readConfig({ PGHOST: 'localhost', PGDATABASE: 'VB-SIF', PGUSER: 'reader', PGPASSWORD: 'x',
    INTERNAL_SCHEDULERS_ENABLED: 'true' }), /dedicated/);
  const environment = workerEnvironment(config);
  assert.equal(environment.PGUSER, 'writer');
  assert.equal(environment.PGPASSWORD, 'writer-test');
  assert.equal(environment.FACTSHEET_MAX_WORKERS, '1');
});

test('database lock prevents another deployment from starting duplicate workers', async () => {
  const client = Object.assign(new EventEmitter(), {
    query: async () => ({ rows: [{ acquired: false }] }), release() {},
  });
  const pool = Object.assign(new EventEmitter(), { connect: async () => client, end: async () => {} });
  let ran = false;
  const scheduler = new InternalScheduler(config, log, async () => { ran = true; return 0; }, pool as unknown as pg.Pool);
  await scheduler.tick();
  assert.equal(ran, false);
  await scheduler.close();
});

test('only one due worker runs per tick and failures persist bounded retry state', async () => {
  const updates: unknown[][] = [];
  const client = Object.assign(new EventEmitter(), {
    query: async (sql: string, values: unknown[] = []) => {
      if (sql.includes('pg_try_advisory_lock')) return { rows: [{ acquired: true }] };
      if (sql.startsWith('SELECT *')) return { rows: ['nav','factsheets'].map((job_id) => ({
        job_id, enabled: true, state: 'not_run', scheduled_for: null, retry_at: null, attempts: 0 })) };
      if (sql.startsWith('UPDATE')) updates.push(values);
      return { rows: [] };
    }, release() {},
  });
  const pool = Object.assign(new EventEmitter(), { connect: async () => client, end: async () => {} });
  const ran: string[] = [];
  const scheduler = new InternalScheduler(config, log, async (job) => { ran.push(job); return 1; },
    pool as unknown as pg.Pool, () => new Date('2026-11-09T06:00:00Z'));
  await scheduler.tick();
  assert.deepEqual(ran, ['nav']);
  assert.equal(updates.length, 2);
  assert.equal(updates[1]?.[3], 'failed');
  assert.equal(updates[1]?.[7], 1);
  assert.ok(updates[1]?.[6] instanceof Date);
  await scheduler.close();
});

test('a due job waiting for the other worker is shown as queued', () => {
  const queued = serverJob({ ...row, next_run_at: new Date('2026-11-09T06:59:00Z') }, 'factsheets', now);
  assert.equal(queued.state, 'queued');
  assert.equal(queued.lastExitCode, 0);
});

test('the oldest due job runs first so repeated NAV failures cannot starve factsheets', async () => {
  const client = Object.assign(new EventEmitter(), {
    query: async (sql: string) => ({ rows: sql.includes('pg_try_advisory_lock') ? [{ acquired: true }]
      : sql.startsWith('SELECT *') ? ['nav','factsheets'].map((job_id) => ({ job_id,
        enabled: true, state: 'failed', scheduled_for: null, retry_at: null, attempts: 0 })) : [] }),
    release() {},
  });
  const pool = Object.assign(new EventEmitter(), { connect: async () => client, end: async () => {} });
  const ran: string[] = [];
  const scheduler = new InternalScheduler(config, log, async (job) => { ran.push(job); return 0; },
    pool as unknown as pg.Pool, () => new Date('2026-11-09T08:31:00Z'));
  await scheduler.tick();
  assert.deepEqual(ran, ['factsheets']);
  await scheduler.close();
});
