import assert from 'node:assert/strict';
import { test } from 'node:test';
import Fastify from 'fastify';
import type pg from 'pg';
import { checkFreshness, getPipelineMonitor, readNavMonitorData, summarizeTask,
  type MonitorData, type TaskSnapshot } from '../src/services/pipeline-monitor.js';
import { registerPipelineMonitor } from '../src/routes/pipeline-monitor.js';

const data: MonitorData = { lastCheckedAt: '2026-11-09T05:32:50Z', freshness: 'fresh',
  summary: '28 of 34 schemes extracted.', metrics: { 'Extracted schemes': '28 / 34' },
  issues: [{ name: 'Publisher', message: 'Publisher access was denied.' }] };
const task: TaskSnapshot = { taskName: 'SIF Factsheet Monitor', readStatus: 'ok', enabled: true,
  state: 'Ready', lastRunAt: '2026-11-09T05:31:59Z', nextRunAt: '2026-11-10T05:31:59Z',
  lastExitCode: 0, triggerTimes: ['11:01:59'] };
const pool = {} as pg.Pool;

test('scheduler state distinguishes successful execution from publisher failures', () => {
  const result = summarizeTask(task, 'factsheets', 'Factsheet Monitor', data);
  assert.equal(result.state, 'succeeded');
  assert.equal(result.schedule, 'Daily at 11:01:59 IST');
  assert.equal(result.data.issues.length, 1);
  assert.equal(summarizeTask({ ...task, lastExitCode: 2147942667 }, '', '', data).taskError,
    'The scheduled task working directory is invalid.');
});

test('scheduler check handles running, disabled, missing, never run and unreadable tasks', () => {
  for (const [override, expected] of [
    [{ state: 'Running' }, 'running'], [{ state: 'Queued' }, 'queued'],
    [{ enabled: false }, 'disabled'], [{ readStatus: 'missing' }, 'missing'],
    [{ lastRunAt: null, lastExitCode: 0x41303 }, 'not_run'],
    [{ readStatus: 'unavailable' }, 'unavailable'],
  ] as const) {
    const result = summarizeTask({ ...task, ...override }, '', '', data);
    assert.equal(result.state, expected);
    if (expected === 'missing' || expected === 'unavailable') assert.equal(result.schedule, 'Unavailable');
  }
  const times = Array.from({ length: 12 }, (_, i) => `${String(i * 2).padStart(2, '0')}:00:00`);
  assert.equal(summarizeTask({ ...task, triggerTimes: times }, '', '', data).schedule, 'Every 2 hours (IST)');
});

test('freshness handles daily and two-hour checks without treating unknown times as current', () => {
  const now = new Date('2026-11-09T08:00:00Z');
  assert.equal(checkFreshness('2026-11-09T05:32:50Z', 26 * 60, now), 'fresh');
  assert.equal(checkFreshness('2026-11-09T04:00:00Z', 180, now), 'stale');
  assert.equal(checkFreshness(null, 180, now), 'unknown');
  assert.equal(checkFreshness('2026-11-10T08:00:00Z', 180, now), 'unknown');
});

test('database failure retains factsheet checks and never exposes database errors', async () => {
  const result = await getPipelineMonitor(pool, 1440, {
    tasks: async () => [task], factsheets: async () => data,
    nav: async () => { throw new Error('secret database credentials'); },
  });
  assert.equal(result.backend.database, 'unavailable');
  assert.equal(result.jobs[0]?.data.metrics['Extracted schemes'], '28 / 34');
  assert.equal(result.jobs[1]?.state, 'unavailable');
  assert.equal(result.jobs[1]?.data.freshness, 'unknown');
  assert.deepEqual(result.jobs[1]?.data.metrics, {});
  assert.ok(!JSON.stringify(result).includes('secret'));
});

test('unreadable task service retains database results without claiming schedules are registered', async () => {
  const result = await getPipelineMonitor(pool, 1440, {
    tasks: async () => { throw new Error('Windows account details'); },
    factsheets: async () => data, nav: async () => data,
  });
  assert.equal(result.backend.database, 'connected');
  assert.ok(result.jobs.every((job) => job.state === 'unavailable' && job.schedule === 'Unavailable'));
  assert.equal(result.jobs[0]?.data.summary, data.summary);
});

test('NAV monitor reports an unchanged check and actual database freshness', async () => {
  const attempt = { startedAt: new Date('2026-11-09T06:30:00Z'), completedAt: new Date('2026-11-09T06:30:20Z'),
    status: 'succeeded', sourceUnchanged: true, insertedCount: 0, updatedCount: 0 };
  const mockPool = { query: async (sql: string) => ({ rows:
    sql.includes('max(nav_date)') ? [{ latestDate: '2026-11-06' }]
      : sql.includes('tigzig_sync_file_versions') ? [{ datasetName: 'nav', databaseRowCount: 9200,
        manifestGeneratedAt: new Date('2026-11-09T06:00:00Z') }]
        : sql.includes('ORDER BY started_at DESC LIMIT 1') ? [attempt] : [] }) } as unknown as pg.Pool;
  const result = await readNavMonitorData(mockPool, 1440, new Date('2026-11-09T07:00:00Z'));
  assert.match(result.summary, /No source changes/);
  assert.equal(result.freshness, 'fresh');
  assert.equal(result.metrics['Latest NAV date'], '2026-11-06');
  assert.equal(result.metrics['Last batch inserted'], 0);
  assert.equal(result.metrics['Stored NAV observations'], 9200);
});

test('read-only public monitor includes both jobs and masks unexpected route errors', async () => {
  const app = Fastify({ logger: false });
  registerPipelineMonitor(app, pool, 1440, async () => getPipelineMonitor(pool, 1440, {
    tasks: async () => [task], factsheets: async () => data, nav: async () => data,
  }));
  try {
    const response = await app.inject({ method: 'GET', url: '/api/sync/status' });
    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.json().data.jobs.map((job: { id: string }) => job.id), ['factsheets', 'nav']);
  } finally { await app.close(); }
  const failed = Fastify({ logger: false });
  registerPipelineMonitor(failed, pool, 1440, async () => { throw new Error('private local path'); });
  try {
    const response = await failed.inject({ method: 'GET', url: '/api/sync/status' });
    assert.equal(response.statusCode, 503);
    assert.ok(!response.body.includes('private local path'));
  } finally { await failed.close(); }
});
