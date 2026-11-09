import Fastify from 'fastify';
import type pg from 'pg';
import { describe, expect, it, vi } from 'vitest';
import { registerSchemePerformance } from '../src/routes/scheme-performance.js';
import { metricRecords, persistSchemePerformance, readSchemeNavSeries } from '../src/repositories/scheme-performance.js';
import { computeSchemePerformance } from '../src/services/scheme-performance-calculator.js';
import { calculateAllSchemesPerformance } from '../src/services/performance.service.js';

function fakePool(rows: Array<Record<string, unknown>> = []) {
  const query = vi.fn(async (_sql?: string, _values?: unknown[]) => ({ rows, rowCount: 0 }));
  const client = { query: vi.fn(async (_sql?: string, _values?: unknown[]) => ({ rows: [], rowCount: 0 })), release: vi.fn() };
  return { query, connect: vi.fn(async () => client), client };
}
const row = (source = 'TEST', schemeCode = '1', plan = 'Regular', nav = '100', date = '2026-11-04') => ({
  source, schemeCode, plan, option: 'Growth', externalSchemeId: null, schemeName: 'API fixture', amc: null, date, nav,
});

describe('repository and transactional persistence', () => {
  it('rounds once for final storage and retains the exact calculated values', () => {
    const result = computeSchemePerformance({
      scheme: { schemeId: 'TEST:1', source: 'TEST', schemeCode: '1', externalSchemeId: null,
        schemeName: 'Precision fixture', plan: 'Regular', option: 'Growth', amc: null },
      observations: [{ date: '2026-10-07', nav: '89999999999999.9999999999' },
        { date: '2026-11-06', nav: '90004499999999.9999999998' }],
    });
    expect(result.periods['1M'].absoluteReturn!.lt('.005')).toBe(true);
    const record = metricRecords([result]).find((item) => item.horizon === '1M')!;
    expect(record.returnPct).toBe('0.00');
    expect(record.metadata.absoluteReturnExact).toBe(result.periods['1M'].absoluteReturn!.toString());
  });
  it('fetches all NAVs in one batch and separates sources, plans and options', async () => {
    const pool = fakePool([row(), row('OTHER', '1', 'Direct'), row('TEST', '2', 'Regular')]);
    const result = await readSchemeNavSeries(pool as unknown as pg.Pool);
    expect(result.map((item) => item.scheme.schemeId)).toEqual(['TEST:1', 'OTHER:1', 'TEST:2']);
    expect(pool.query).toHaveBeenCalledTimes(1);
  });
  it('commits both metric caches in a single transaction using bulk inserts', async () => {
    const pool = fakePool([row(), row('TEST', '1', 'Regular', '101', '2026-11-05'), row('TEST', '1', 'Regular', '99', '2026-11-06')]);
    await calculateAllSchemesPerformance(pool as unknown as pg.Pool);
    const sql = pool.client.query.mock.calls.map((call) => call[0]);
    expect(sql[0]).toBe('BEGIN');
    expect(sql.filter((statement) => statement?.includes('INSERT INTO')).length).toBe(2);
    expect(sql.at(-1)).toBe('COMMIT');
    expect(pool.client.release).toHaveBeenCalledOnce();
  });
  it('rolls back both caches when either write fails and releases the connection', async () => {
    const pool = fakePool();
    pool.client.query.mockImplementation(async (sql) => {
      if (sql?.includes('INSERT INTO public.sif_scheme_risk_cache')) throw new Error('write failed');
      return { rows: [], rowCount: 0 };
    });
    const result = computeSchemePerformance({
      scheme: { schemeId: 'TEST:1', source: 'TEST', schemeCode: '1', externalSchemeId: null,
        schemeName: 'API fixture', plan: 'Regular', option: 'Growth', amc: null },
      observations: [{ date: '2026-11-05', nav: '100' }, { date: '2026-11-06', nav: '101' }],
    });
    await expect(persistSchemePerformance(pool as unknown as pg.Pool, [result])).rejects.toThrow('write failed');
    expect(pool.client.query.mock.calls.at(-1)![0]).toBe('ROLLBACK');
    expect(pool.client.release).toHaveBeenCalledOnce();
  });
  it('logs a bad scheme and still calculates and persists the other schemes', async () => {
    const pool = fakePool([row(), row('TEST', '1', 'Regular', '101'), row('TEST', '2'), row('TEST', '2', 'Regular', '110', '2026-11-06')]);
    const onError = vi.fn();
    const result = await calculateAllSchemesPerformance(pool as unknown as pg.Pool, { onError });
    expect(result.totalSchemes).toBe(2);
    expect(result.schemes.map((item) => item.scheme.schemeId)).toEqual(['TEST:2']);
    expect(result.errors[0]!.schemeId).toBe('TEST:1');
    expect(onError).toHaveBeenCalledOnce();
  });
});

describe('Fastify performance API', () => {
  function appWith(pool = fakePool(), key: string | null = 'test-key') {
    const app = Fastify(); registerSchemePerformance(app, pool as unknown as pg.Pool, key); return app;
  }
  it('requires the admin key for calculation requests', async () => {
    const pool = fakePool(), app = appWith(pool);
    try {
      expect((await app.inject({ method: 'POST', url: '/api/performance/calculate-all' })).statusCode).toBe(401);
      expect(pool.query).not.toHaveBeenCalled();
    } finally { await app.close(); }
  });
  it.each(['2026-02-30', '2026-11-06T12:00:00Z', '11/06/2026', 'bad'])('rejects invalid as-of date %s', async (asOfDate) => {
    const app = appWith();
    try { expect((await app.inject({ method: 'POST', url: '/api/performance/calculate-all',
      headers: { authorization: 'Bearer test-key' }, payload: { asOfDate } })).statusCode).toBe(400); }
    finally { await app.close(); }
  });
  it('calculates a scheme without requiring a body and preserves the response contract', async () => {
    const pool = fakePool([row(), row('TEST', '1', 'Regular', '110', '2026-11-05'), row('TEST', '1', 'Regular', '99', '2026-11-06')]);
    const app = appWith(pool);
    try {
      const response = await app.inject({ method: 'POST', url: '/api/performance/TEST:1/calculate', headers: { authorization: 'Bearer test-key' } });
      expect(response.statusCode).toBe(200);
      const body = response.json();
      expect(body.data).toMatchObject({ schemeId: 'TEST:1', asOfDate: '2026-11-06', currentNav: 99 });
      expect(body.data.periods.SI).toMatchObject({ absoluteReturn: -1, annualizedVolatility: 224.5, observationCount: 2 });
    } finally { await app.close(); }
  });
  it('distinguishes unknown and ambiguous scheme identifiers', async () => {
    for (const [rows, expected] of [[[], 404], [[row(), row('OTHER')], 409]] as const) {
      const app = appWith(fakePool([...rows]));
      try { expect((await app.inject({ method: 'POST', url: '/api/performance/1/calculate',
        headers: { authorization: 'Bearer test-key' } })).statusCode).toBe(expected); }
      finally { await app.close(); }
    }
  });
  it('validates period filters and returns 404 for uncalculated metrics', async () => {
    const app = appWith();
    try {
      expect((await app.inject('/api/performance?period=1W')).statusCode).toBe(400);
      expect((await app.inject('/api/performance?asOfDate=2026-02-30')).statusCode).toBe(400);
      expect((await app.inject('/api/performance/TEST:1?period=1M')).statusCode).toBe(404);
    } finally { await app.close(); }
  });
});
