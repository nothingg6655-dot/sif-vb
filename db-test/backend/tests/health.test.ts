import assert from 'node:assert/strict';
import { test } from 'node:test';
import Fastify from 'fastify';
import type pg from 'pg';
import { registerHealth } from '../src/routes/health.js';
import { verifyDatabase } from '../src/config/database.js';

test('database identity checks use the configured target and still reject wrong schema', async () => {
  const pool = (database: string, schema: string) => ({
    query: async () => ({ rows: [{ database, schema }] }),
  }) as unknown as pg.Pool;
  await verifyDatabase(pool('postgres', 'public'), 'postgres');
  await assert.rejects(verifyDatabase(pool('VB-SIF', 'public'), 'postgres'));
  await assert.rejects(verifyDatabase(pool('postgres', 'auth'), 'postgres'));
});

test('health returns 503 without exposing database connection errors', async () => {
  const app = Fastify({ logger: false });
  const unavailablePool = {
    query: async () => { throw new Error('Connection failed: secret-credential'); }
  } as unknown as pg.Pool;
  registerHealth(app, unavailablePool);
  try {
    const response = await app.inject({ method: 'GET', url: '/health' });
    assert.equal(response.statusCode, 503);
    assert.deepEqual(response.json(), { status: 'unavailable', database: 'unavailable' });
    assert.ok(!response.body.includes('secret-credential'));
  } finally { await app.close(); }
});
