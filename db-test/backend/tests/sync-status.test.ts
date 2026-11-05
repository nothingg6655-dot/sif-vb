import assert from 'node:assert/strict';
import { test } from 'node:test';
import Fastify from 'fastify';
import type pg from 'pg';
import { registerAdminSync } from '../src/routes/admin-sync.js';

test('admin sync status hides database error details', async () => {
  const app = Fastify({ logger: false });
  const pool = { query: async () => { throw new Error('secret credential'); } } as unknown as pg.Pool;
  registerAdminSync(app, pool, 1440, null);
  try {
    const response = await app.inject({ method: 'GET', url: '/api/admin/tigzig/sync/status' });
    assert.equal(response.statusCode, 503);
    assert.deepEqual(response.json(), {
      success: false,
      error: { code: 'SYNC_STATUS_UNAVAILABLE', message: 'Sync status is unavailable' }
    });
    assert.ok(!response.body.includes('secret credential'));
  } finally { await app.close(); }
});

test('configured admin endpoint requires a bearer key before querying', async () => {
  const app = Fastify({ logger: false });
  let queried = false;
  const pool = { query: async () => { queried = true; return { rows: [] }; } } as unknown as pg.Pool;
  registerAdminSync(app, pool, 1440, 'expected-key');
  try {
    const denied = await app.inject({ method: 'GET', url: '/api/admin/tigzig/sync/status' });
    assert.equal(denied.statusCode, 401);
    assert.equal(queried, false);
    const allowed = await app.inject({
      method: 'GET', url: '/api/admin/tigzig/sync/status',
      headers: { authorization: 'Bearer expected-key' }
    });
    assert.equal(allowed.statusCode, 200);
    assert.equal(queried, true);
  } finally { await app.close(); }
});
