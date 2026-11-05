import assert from 'node:assert/strict';
import { test } from 'node:test';
import Fastify from 'fastify';
import type pg from 'pg';
import { buildApp } from '../src/app.js';
import { readConfig } from '../src/config/env.js';
import { createPool } from '../src/config/database.js';
import type { AllocationRow } from '../src/repositories/allocations.js';
import { registerFundAllocationRoutes } from '../src/routes/allocations.js';

const endpoints = [
  ['asset-allocation', 'allocation', 'ASSET', 'Asset Allocation (%)'],
  ['market-cap-allocation', 'market-cap', 'MARKET_CAP', 'Market Capitalization (%)'],
  ['sector-allocation', 'sector-allocation', 'SECTOR', 'Sector Allocation (%)'],
  ['rating-wise-allocation', 'rating-allocation', 'RATING', 'Rating Profile (%)'],
] as const;

function url(endpoint: string, name: string) {
  return `/api/${endpoint}?${new URLSearchParams({ scheme_name: name })}`;
}

test('name endpoints reconcile all registered strategy and verified plan names with their own allocations', async () => {
  const config = readConfig({ ...process.env, LOG_LEVEL: 'silent' });
  const pool = createPool(config);
  const app = await buildApp(config);
  try {
    const names = (await pool.query<{ strategyId: number; name: string }>(`
      SELECT strategy_id::integer AS "strategyId",strategy_name AS name FROM public.sif_strategies
      UNION
      SELECT strategy_id::integer,scheme_name FROM public.vw_sif_verified_strategy_schemes
      ORDER BY "strategyId",name
    `)).rows;
    assert.ok(names.length >= 34);
    for (const [endpoint, type] of endpoints) {
      const bulk = await app.inject(`/api/funds/allocations/${type}`);
      assert.equal(bulk.statusCode, 200);
      const byId = new Map<number, any>(bulk.json().data.map((fund: any) => [fund.strategyId, fund]));
      for (const { strategyId, name } of names) {
        const expected = byId.get(strategyId);
        assert.ok(expected, name);
        const response = await app.inject(url(endpoint, name));
        if (expected.reportId === null) {
          assert.equal(response.statusCode, 404, name);
          assert.equal(response.json().error.code, 'FACTSHEET_NOT_FOUND');
        } else {
          assert.equal(response.statusCode, 200, `${name}: ${response.body}`);
          assert.deepEqual(response.json(), { success: true, data: expected }, name);
        }
      }
    }
  } finally { await app.close(); await pool.end(); }
});

test('name matching accepts case, punctuation, and spacing variations but refuses partial and unverified names', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const baseline = await app.inject('/api/funds/dynasif-equity-long-short-fund/market-cap');
    assert.equal(baseline.statusCode, 200);
    for (const name of [
      '  dYnAsIf   EQUITY  Long – Short Fund  ',
      'DynaSIF Equity Long Short Fund',
      'DynaSIF Equity Long - Short Fund - Direct Plan - Growth Option',
      'DynaSIF Equity Long - Short Fund - Regular Plan - IDCW Option',
    ]) {
      const response = await app.inject(url('market-cap-allocation', name));
      assert.equal(response.statusCode, 200, response.body);
      assert.deepEqual(response.json(), baseline.json());
    }
    for (const name of [
      'DynaSIF', 'Long Short Fund', 'Nonexistent Fund', "' OR 1=1 --",
      'Titanium Active Asset Allocator Long-Short Fund Direct Growth',
    ]) {
      const response = await app.inject(url('asset-allocation', name));
      assert.equal(response.statusCode, 404, name);
      assert.equal(response.json().error.code, 'SCHEME_NOT_FOUND');
    }
  } finally { await app.close(); }
});

test('Arthaya official September report exposes allocations with published denominators', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    for (const [endpoint, total, availability] of [
      ['asset-allocation', 100, 'PUBLISHED'],
      ['market-cap-allocation', 73.12, 'PUBLISHED'],
      ['sector-allocation', 73.12, 'PUBLISHED'],
      ['rating-wise-allocation', null, 'NOT_PUBLISHED'],
    ] as const) {
      const response = await app.inject(url(endpoint, 'Arthaya Equity Long-Short Fund'));
      assert.equal(response.statusCode, 200, response.body);
      const { data } = response.json();
      assert.equal(data.reportMonth, '2026-09');
      assert.equal(data.portfolioAsOf, '2026-09-30');
      assert.equal(data.totalPercent, total);
      assert.equal(data.availability, availability);
      assert.equal(data.source.validationStatus, 'PASSED');
      assert.ok(data.source.pdfUrl.startsWith('https://www.unionmf.com/'));
      const values = Object.fromEntries(data.items.map((item: { label: string; percent: number }) =>
        [item.label, item.percent]));
      if (endpoint === 'market-cap-allocation') {
        assert.deepEqual(values, { 'Large Cap': 50.95, 'Mid Cap': 17.01, 'Small Cap': 5.16 });
      }
      if (endpoint === 'sector-allocation') {
        assert.equal(data.items.length, 17);
        assert.equal(values.BANKS, 28.07);
        assert.equal(values['AGRICULTURAL, COMMERCIAL & CONSTRUCTION VEHICLES'], 0);
      }
      if (endpoint === 'rating-wise-allocation') assert.deepEqual(data.items, []);
    }
  } finally { await app.close(); }
});

const row: AllocationRow = {
  strategyId: 11, fullName: 'DynaSIF Equity Long-Short Fund',
  slug: 'dynasif-equity-long-short-fund', reportId: 8,
  reportMonth: '2026-09', portfolioAsOf: '2026-09-30',
  validationStatus: 'PASSED', pdfUrl: null, latestListingVerified: false,
  availability: 'PUBLISHED', label: 'Large Cap', percent: '47.10000000',
  basis: null, sourcePage: null, publishedOrder: 1,
};

function fixture(rows: AllocationRow[], failure = false) {
  const calls: unknown[][] = [];
  const pool = {
    async query(_sql: string, params: unknown[]) {
      calls.push(params);
      if (failure) throw new Error('private database error');
      return { rows };
    },
  } as unknown as pg.Pool;
  const app = Fastify({ logger: false });
  registerFundAllocationRoutes(app, pool);
  return { app, calls };
}

test('all four endpoints validate scheme_name before querying and pass normalized names as query parameters', async () => {
  const { app, calls } = fixture([row]);
  try {
    for (const [endpoint, , family, field] of endpoints) {
      for (const suffix of [
        '', '?scheme_name=', '?scheme_name=%20%20', '?scheme_name=---',
        '?scheme_name=A&scheme_name=B', `?scheme_name=${'A'.repeat(501)}`,
      ]) {
        const response = await app.inject(`/api/${endpoint}${suffix}`);
        assert.equal(response.statusCode, 400, response.body);
        assert.equal(response.json().error.code, 'INVALID_QUERY');
      }
      const response = await app.inject(url(endpoint, ' DynaSIF Equity Long - Short Fund '));
      assert.equal(response.statusCode, 200);
      assert.deepEqual(calls.at(-1), ['dynasifequitylongshortfund', family, field]);
    }
    assert.equal(calls.length, 4);
  } finally { await app.close(); }
});

test('name endpoints distinguish unknown, ambiguous, missing-factsheet, and database errors', async () => {
  for (const [rows, failure, status, code] of [
    [[], false, 404, 'SCHEME_NOT_FOUND'],
    [[row, { ...row, strategyId: 99 }], false, 409, 'AMBIGUOUS_SCHEME'],
    [[{ ...row, reportId: null }], false, 404, 'FACTSHEET_NOT_FOUND'],
    [[], true, 500, 'ALLOCATION_UNAVAILABLE'],
  ] as const) {
    const { app } = fixture([...rows], failure);
    try {
      for (const [endpoint] of endpoints) {
        const response = await app.inject(url(endpoint, row.fullName));
        assert.equal(response.statusCode, status);
        assert.equal(response.json().success, false);
        assert.equal(response.json().error.code, code);
        assert.ok(!response.body.includes('private database error'));
      }
    } finally { await app.close(); }
  }
});
