import assert from 'node:assert/strict';
import { test } from 'node:test';
import type pg from 'pg';
import { buildApp } from '../src/app.js';
import { createPool } from '../src/config/database.js';
import { readConfig } from '../src/config/env.js';
import { readLiveTicker, readPerformanceNav } from '../src/repositories/funds.js';
import { readComparisonMetadata } from '../src/repositories/category-comparison.js';

test('public analytics, fund variants and NAV history expose Regular plans only', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    for (const endpoint of ['/api/analytics/performance', '/api/analytics/monthly-heatmap?range=all',
      '/api/analytics/category-comparison']) {
      const response = await app.inject(endpoint);
      assert.equal(response.statusCode, 200);
      const funds = response.json().funds;
      assert.ok(funds.length > 0);
      for (const fund of funds) assert.equal(fund.plan ?? fund.scheme?.plan, 'Regular');
    }
    const response = await app.inject('/api/funds/magnum-hybrid-long-short-fund');
    assert.equal(response.statusCode, 200);
    const details = response.json().data;
    assert.ok(details.schemes.length > 0);
    assert.ok(details.schemes.every((scheme: { plan: string }) => scheme.plan === 'Regular'));
    assert.ok(details.quarterlyAAUM.every((row: { schemeName: string }) => !/direct/i.test(row.schemeName)));
    assert.equal((await app.inject('/api/nav-history?sd_id=SIF-14&from_date=2026-10-01&to_date=2026-11-06')).statusCode, 404);
    assert.equal((await app.inject('/api/nav-history?sd_id=SIF-13&from_date=2026-10-01&to_date=2026-11-06')).statusCode, 200);
  } finally { await app.close(); }
});

test('a misconfigured Direct mapping is excluded rather than labelled Regular', async () => {
  const pool = createPool(readConfig());
  const connection = await pool.connect();
  try {
    await connection.query('BEGIN');
    const candidate = (await connection.query<{
      referenceId: number; fundId: number; source: string; schemeCode: number;
    }>(`SELECT c.reference_fund_id AS "referenceId",c.fund_id::integer AS "fundId",
               s.source,s.scheme_code::integer AS "schemeCode"
        FROM public.app_performance_config c
        JOIN public.app_tigzig_fund_mapping m USING(fund_id)
        JOIN public.tigzig_schemes s USING(source,scheme_code)
        WHERE c.selection_verified AND s.scheme_plan='Direct' AND s.scheme_option='Growth'
        ORDER BY c.reference_fund_id LIMIT 1`)).rows[0]!;
    assert.ok(candidate);
    await connection.query(`UPDATE public.app_performance_config
      SET preferred_source=$1,preferred_scheme_code=$2 WHERE reference_fund_id=$3`,
      [candidate.source, candidate.schemeCode, candidate.referenceId]);
    const repository = connection as unknown as pg.Pool;
    assert.equal((await readPerformanceNav(repository, '2026-11-06', candidate.referenceId)).length, 0);
    assert.ok((await readLiveTicker(repository)).every((row) => row.fundId !== candidate.fundId));
    assert.ok((await readComparisonMetadata(repository, '2026-11-06')).every((row) => row.fundId !== candidate.fundId));
  } finally {
    await connection.query('ROLLBACK');
    connection.release();
    await pool.end();
  }
});
