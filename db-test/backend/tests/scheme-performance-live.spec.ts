import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { buildApp } from '../src/app.js';
import { readConfig } from '../src/config/env.js';
import { createPool } from '../src/config/database.js';
import { PERFORMANCE_VERSION } from '../src/services/scheme-performance-model.js';

describe.skipIf(process.env.RUN_LIVE_PERFORMANCE_TESTS !== 'true')('live database and API integration', () => {
  const config = { ...readConfig(), logLevel: 'silent', adminApiKey: 'live-test-calculation-key' };
  const pool = createPool(config);
  let app: Awaited<ReturnType<typeof buildApp>>;
  beforeAll(async () => { app = await buildApp(config); });
  afterAll(async () => { await app.close(); await pool.end(); });
  it('returns independently stored metrics for all source schemes and every plan/option', async () => {
    const response = await app.inject('/api/performance?asOfDate=2026-11-06');
    expect(response.statusCode).toBe(200);
    const expected = Number((await pool.query('SELECT count(*) FROM public.tigzig_schemes')).rows[0].count);
    expect(response.json().count).toBe(expected);
    expect(new Set(response.json().data.map((scheme: { plan: string }) => scheme.plan))).toEqual(new Set(['Direct', 'Regular']));
    expect(new Set(response.json().data.map((scheme: { option: string }) => scheme.option))).toEqual(new Set(['Growth', 'IDCW']));
  });
  it.each([['SIF:7', -1.03], ['SIF:11', .30], ['SIF:13', 1.27], ['SIF:29', 4.73],
    ['SIF:40', .41], ['SIF:35', 3.06], ['SIF:80', 1.28]])('exposes the CSV-validated 1M result for %s', async (id, expected) => {
    const response = await app.inject(`/api/performance/${id}?asOfDate=2026-11-06&period=1M`);
    expect(response.statusCode).toBe(200);
    expect(Object.keys(response.json().data.periods)).toEqual(['1M']);
    expect(response.json().data.periods['1M'].absoluteReturn).toBe(expected);
    expect(response.json().data.periods['1M'].startingNavDate).toBe('2026-10-07');
  });
  it('recalculates idempotently without changing stored timestamps', async () => {
    const before = await pool.query('SELECT count(*)::int AS count,max(updated_at)::text AS updated FROM public.sif_scheme_return_cache WHERE calculation_version=$1', [PERFORMANCE_VERSION]);
    const response = await app.inject({ method: 'POST', url: '/api/performance/calculate-all',
      headers: { authorization: 'Bearer live-test-calculation-key' }, payload: { asOfDate: '2026-11-06' } });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({ success: true, returnRowsChanged: 0, riskRowsChanged: 0, errors: [] });
    const after = await pool.query('SELECT count(*)::int AS count,max(updated_at)::text AS updated FROM public.sif_scheme_return_cache WHERE calculation_version=$1', [PERFORMANCE_VERSION]);
    expect(after.rows).toEqual(before.rows);
  });
  it('retains a separately calculated historical date', async () => {
    const response = await app.inject({ method: 'POST', url: '/api/performance/SIF:7/calculate',
      headers: { authorization: 'Bearer live-test-calculation-key' }, payload: { asOfDate: '2026-11-05' } });
    expect(response.statusCode).toBe(200);
    const dates = await pool.query("SELECT DISTINCT as_of_date::text AS date FROM public.sif_scheme_return_cache WHERE source='SIF' AND scheme_code=7 AND calculation_version=$1", [PERFORMANCE_VERSION]);
    expect(dates.rows.map((row: { date: string }) => row.date)).toEqual(expect.arrayContaining(['2026-11-05', '2026-11-06']));
  });
  it('makes the existing overview agree with the scheme engine', async () => {
    const response = await app.inject('/api/analytics/category-comparison?to_date=2026-11-06');
    expect(response.statusCode).toBe(200);
    const fund = response.json().funds.find((row: { slug: string }) => row.slug === 'magnum-hybrid-long-short-fund');
    expect(fund.returns.r1m.value).toBe(1.27);
    const metrics = (await app.inject('/api/performance/SIF:13?asOfDate=2026-11-06')).json().data;
    expect(fund.volatility.r1m.value).toBe(metrics.periods['1M'].annualizedVolatility);
  });
});
