import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildApp } from '../src/app.js';
import { readConfig } from '../src/config/env.js';

test('database changes require an explicitly matching expected target', () => {
  assert.throws(() => readConfig({ ...process.env, PGDATABASE: 'postgres', PGEXPECTED_DATABASE: 'VB-SIF' }), /PGEXPECTED_DATABASE/);
  const configured = readConfig({ ...process.env, PGDATABASE: 'postgres', PGEXPECTED_DATABASE: 'postgres' });
  assert.equal(configured.database.database, 'postgres');
});

test('pool size must be bounded', () => {
  assert.throws(() => readConfig({ ...process.env, PGPOOL_MAX: '1000' }), /PGPOOL_MAX/);
});

test('health and read-only sync monitoring connect to the real database', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const response = await app.inject({ method: 'GET', url: '/health' });
    assert.equal(response.statusCode, 200);
    assert.deepEqual(response.json(), { status: 'ok', database: 'connected' });
    const syncStatus = await app.inject({ method: 'GET', url: '/api/admin/tigzig/sync/status' });
    assert.equal(syncStatus.statusCode, 200);
    const body = syncStatus.json();
    assert.equal(body.success, true);
    assert.ok(Array.isArray(body.data.datasets));
    assert.equal(typeof body.data.sourceFreshness.status, 'string');
    const ticker = await app.inject({ method: 'GET', url: '/api/liveTicker' });
    assert.equal(ticker.statusCode, 200);
    assert.equal(ticker.json().data.length, 34);
  } finally { await app.close(); }
});
