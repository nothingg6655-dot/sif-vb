import assert from 'node:assert/strict';
import { test } from 'node:test';
import Fastify from 'fastify';
import type pg from 'pg';
import { buildApp } from '../src/app.js';
import { readConfig } from '../src/config/env.js';
import { registerFundAllocationRoutes } from '../src/routes/allocations.js';
import type { AllocationRow } from '../src/repositories/allocations.js';
import { allocationDefinitions } from '../src/services/allocations.js';

const slug = 'dynasif-equity-long-short-fund';
const base: AllocationRow = {
  strategyId: 11, fullName: 'DynaSIF Equity Long-Short Fund', slug,
  reportId: 8, reportMonth: '2026-09', portfolioAsOf: '2026-09-30',
  validationStatus: 'PASSED', pdfUrl: 'https://example.com/factsheet.pdf',
  latestListingVerified: false, availability: 'PUBLISHED',
  label: 'Equity & Equity Related', percent: '76.30000000', basis: null,
  sourcePage: null, publishedOrder: 1,
};

test('all three Dynasif APIs reconcile with stored September factsheet allocations', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const expected = {
      allocation: {
        'Equity & Equity Related': 76.3, INVIT: 0.1, 'Treasury Bill': 2.46,
        Derivatives: -1.2, 'Index / Stock Options': 0.2,
        'Net Receivables / (Payables)': 22.14,
      },
      'market-cap': { 'Large Cap': 47.1, 'Mid Cap': 15.71, 'Small Cap': 37.2 },
      'sector-allocation': {
        'Financial Services': 22.99, 'Capital Goods': 8.78, 'Consumer Services': 8.6,
        'Automobile and Auto Components': 7.56, 'Fast Moving Consumer Goods': 6.59,
        Services: 4.48, Healthcare: 4.34, Telecommunication: 3.87,
        'Consumer Durables': 1.96, Power: 1.91, 'Oil Gas & Consumable Fuels': 1.38,
        'Information Technology': 1.13, 'Construction Materials': 1.01,
        Realty: 1, Construction: 0.68,
      },
    };
    for (const [type, values] of Object.entries(expected)) {
      const response = await app.inject({ method: 'GET', url: `/api/funds/${slug}/${type}` });
      assert.equal(response.statusCode, 200, response.body);
      const { success, data } = response.json();
      assert.equal(success, true);
      assert.equal(data.slug, slug);
      assert.equal(data.allocationType, type);
      assert.equal(data.availability, 'PUBLISHED');
      assert.equal(data.reportMonth, '2026-09');
      assert.equal(data.portfolioAsOf, '2026-09-30');
      assert.deepEqual(Object.fromEntries(data.items.map(
        (item: { label: string; percent: number }) => [item.label, item.percent],
      )), values);
      assert.equal(data.totalPercent,
        Number(Object.values(values).reduce((sum, value) => sum + value, 0).toFixed(8)));
      assert.ok(data.source.pdfUrl.startsWith('https://'));
      assert.ok(['PASSED', 'WARNING'].includes(data.source.validationStatus));
      const percents = data.items.map((item: { percent: number }) => item.percent);
      assert.deepEqual(percents, [...percents].sort((a, b) => b - a));
    }
  } finally { await app.close(); }
});

test('rating API distinguishes unpublished Dynasif data from published rating buckets', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const response = await app.inject(`/api/funds/${slug}/rating-allocation`);
    assert.equal(response.statusCode, 200, response.body);
    const { success, data } = response.json();
    assert.equal(success, true);
    assert.equal(data.slug, slug);
    assert.equal(data.allocationType, 'rating-allocation');
    assert.equal(data.reportMonth, '2026-09');
    assert.equal(data.portfolioAsOf, '2026-09-30');
    assert.equal(data.availability, 'NOT_PUBLISHED');
    assert.equal(data.totalPercent, null);
    assert.deepEqual(data.items, []);
    assert.ok(data.source.pdfUrl.startsWith('https://'));

    const published = await app.inject('/api/funds/arudha-hybrid-long-short-fund/rating-allocation');
    assert.equal(published.statusCode, 200, published.body);
    const ratings = published.json().data;
    assert.equal(ratings.allocationType, 'rating-allocation');
    assert.equal(ratings.availability, 'PUBLISHED');
    assert.equal(ratings.totalPercent, 100);
    assert.deepEqual(ratings.items.map((item: { label: string; percent: number }) =>
      ({ label: item.label, percent: item.percent })), [
      { label: 'AAA Equivalent', percent: 89.74 },
      { label: 'AA+', percent: 5.17 },
      { label: 'AA', percent: 5.09 },
    ]);
  } finally { await app.close(); }
});

test('bulk APIs include all 34 funds and reconcile every fund with its individual endpoint', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    let registeredSlugs: string[] | undefined;
    for (const type of Object.keys(allocationDefinitions)) {
      const response = await app.inject(`/api/funds/allocations/${type}`);
      assert.equal(response.statusCode, 200, response.body);
      const { success, count, data } = response.json();
      assert.equal(success, true);
      assert.equal(count, 34);
      assert.equal(data.length, count);
      const slugs = data.map((fund: { slug: string }) => fund.slug);
      assert.equal(new Set(slugs).size, count);
      if (registeredSlugs) assert.deepEqual(slugs, registeredSlugs);
      registeredSlugs = slugs;
      for (const fund of data) {
        assert.equal(fund.allocationType, type);
        const individual = await app.inject(`/api/funds/${fund.slug}/${type}`);
        if (fund.reportId === null) {
          assert.equal(fund.availability, 'MISSING');
          assert.equal(fund.reportMonth, null);
          assert.equal(fund.portfolioAsOf, null);
          assert.equal(fund.totalPercent, null);
          assert.deepEqual(fund.items, []);
          assert.equal(individual.statusCode, 404);
          assert.equal(individual.json().error.code, 'FACTSHEET_NOT_FOUND');
        } else {
          assert.equal(individual.statusCode, 200);
          assert.deepEqual(fund, individual.json().data, `${fund.slug}/${type}`);
        }
      }
    }
  } finally { await app.close(); }
});

function fixtureApp(rows: AllocationRow[], failure = false) {
  const app = Fastify({ logger: false });
  const calls: unknown[][] = [];
  const pool = {
    async query(_sql: string, params: unknown[]) {
      calls.push(params);
      if (failure) throw new Error('private database error');
      return { rows };
    },
  } as unknown as pg.Pool;
  registerFundAllocationRoutes(app, pool);
  return { app, calls };
}

test('allocation routes select their own family and reject malformed slugs before querying', async () => {
  const { app, calls } = fixtureApp([base]);
  try {
    for (const [type, family, field] of [
      ['allocation', 'ASSET', 'Asset Allocation (%)'],
      ['market-cap', 'MARKET_CAP', 'Market Capitalization (%)'],
      ['sector-allocation', 'SECTOR', 'Sector Allocation (%)'],
      ['rating-allocation', 'RATING', 'Rating Profile (%)'],
    ]) {
      const valid = await app.inject(`/api/funds/${slug}/${type}`);
      assert.equal(valid.statusCode, 200);
      assert.deepEqual(calls.at(-1), [slug, family, field]);
      const invalid = await app.inject(`/api/funds/Bad_Slug/${type}`);
      assert.equal(invalid.statusCode, 400);
      assert.equal(invalid.json().error.code, 'INVALID_SLUG');
    }
    assert.equal(calls.length, 4);
  } finally { await app.close(); }
});

test('bulk requests query once per family and keep identical labels in different funds separate', async () => {
  const { app, calls } = fixtureApp([
    base, { ...base, strategyId: 99, fullName: 'Other Fund', slug: 'other-fund', percent: '25.00000000' },
  ]);
  try {
    for (const [type, definition] of Object.entries(allocationDefinitions)) {
      const response = await app.inject(`/api/funds/allocations/${type}`);
      assert.equal(response.statusCode, 200);
      assert.deepEqual(calls.at(-1), [null, definition.family, definition.field]);
      const { count, data } = response.json();
      assert.equal(count, 2);
      assert.deepEqual(data.map((fund: { totalPercent: number }) => fund.totalPercent), [76.3, 25]);
      assert.deepEqual(data.map((fund: { items: unknown[] }) => fund.items.length), [1, 1]);
    }
    assert.equal(calls.length, 4);
  } finally { await app.close(); }
});

test('bulk endpoints distinguish empty registry from database failure', async () => {
  for (const failure of [false, true]) {
    const { app } = fixtureApp([], failure);
    try {
      for (const type of Object.keys(allocationDefinitions)) {
        const response = await app.inject(`/api/funds/allocations/${type}`);
        if (failure) {
          assert.equal(response.statusCode, 500);
          assert.equal(response.json().error.code, 'ALLOCATION_UNAVAILABLE');
          assert.ok(!response.body.includes('private database error'));
        } else {
          assert.equal(response.statusCode, 200);
          assert.deepEqual(response.json(), { success: true, count: 0, data: [] });
        }
      }
    } finally { await app.close(); }
  }
});

test('unknown funds, absent reports, and ambiguous strategies have distinct errors', async () => {
  for (const [rows, status, code] of [
    [[], 404, 'FUND_NOT_FOUND'],
    [[{ ...base, reportId: null }], 404, 'FACTSHEET_NOT_FOUND'],
    [[base, { ...base, strategyId: 99 }], 409, 'AMBIGUOUS_FUND'],
  ] as const) {
    const { app } = fixtureApp([...rows]);
    try {
      for (const type of ['allocation', 'market-cap', 'sector-allocation', 'rating-allocation']) {
        const response = await app.inject(`/api/funds/${slug}/${type}`);
        assert.equal(response.statusCode, status);
        assert.deepEqual(response.json().error.code, code);
      }
    } finally { await app.close(); }
  }
});

test('unpublished and failed extraction remain explicit without fabricated percentages', async () => {
  for (const availability of ['NOT_PUBLISHED', 'PARSE_FAILED']) {
    const { app } = fixtureApp([{ ...base, availability, label: null, percent: null }]);
    try {
      const response = await app.inject(`/api/funds/${slug}/market-cap`);
      assert.equal(response.statusCode, 200);
      assert.equal(response.json().data.availability, availability);
      assert.equal(response.json().data.totalPercent, null);
      assert.deepEqual(response.json().data.items, []);
    } finally { await app.close(); }
  }
});

test('signed exposure and zero are preserved; incomplete or mixed bases have no combined total', async () => {
  for (const [rows, total] of [
    [[{ ...base, percent: '-1.20000000' }, { ...base, label: 'Options', percent: '0.00000000' }], -1.2],
    [[base, { ...base, label: 'Missing', percent: null }], null],
    [[{ ...base, basis: 'Gross' }, { ...base, basis: 'Net' }], null],
  ] as const) {
    const { app } = fixtureApp([...rows]);
    try {
      const response = await app.inject(`/api/funds/${slug}/allocation`);
      assert.equal(response.statusCode, 200);
      const data = response.json().data;
      assert.equal(data.totalPercent, total);
      assert.deepEqual(data.items.map((item: { percent: number | null }) => item.percent),
        rows.map((row) => row.percent === null ? null : Number(row.percent)));
    } finally { await app.close(); }
  }
});

test('database failure returns a stable API error without leaking database details', async () => {
  const { app } = fixtureApp([], true);
  try {
    const response = await app.inject(`/api/funds/${slug}/allocation`);
    assert.equal(response.statusCode, 500);
    assert.equal(response.json().error.code, 'ALLOCATION_UNAVAILABLE');
    assert.ok(!response.body.includes('private database error'));
  } finally { await app.close(); }
});
