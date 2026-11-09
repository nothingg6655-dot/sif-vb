import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildApp } from '../src/app.js';
import { readConfig } from '../src/config/env.js';
import { annualizedVolatility, calculateCategoryComparison } from '../src/services/category-comparison.js';
import { calculateUnifiedPerformance } from '../src/services/analytics.js';
import type { PreferredNavRow } from '../src/repositories/funds.js';
import type { ComparisonMetadata } from '../src/repositories/category-comparison.js';

const rows: PreferredNavRow[] = ['100', '110', '99'].map((nav, i) => ({
  displayOrder: 1, fundId: 1, fullName: 'Test Hybrid Fund', slug: 'test-hybrid',
  source: 'SIF', schemeCode: 1, date: `2026-10-0${i + 1}`, nav,
}));
const metadata: ComparisonMetadata = {
  fundId: 1, fullName: 'Test Hybrid Fund', slug: 'test-hybrid', amc: 'Test AMC',
  strategy: 'Hybrid Long-Short', category: 'Hybrid', launchDate: null,
  netAumCr: null, aumAsOf: null, riskBand: null, riskAsOf: null, returnProfile: null, pdfUrl: null,
};

test('volatility uses sample SD of daily percentage returns and sqrt(252)', () => {
  const metric = calculateUnifiedPerformance(rows, '2026-10-03').funds[0]!.returns.si;
  const result = annualizedVolatility(rows, metric);
  assert.equal(result.value, 224.5); // sample SD([+10%, -10%]) × sqrt(252)
  assert.equal(result.observations, 2);
  const flat = rows.map((row) => ({ ...row, nav: '100' }));
  assert.equal(annualizedVolatility(flat, metric).value, 0);
});

test('available history and sparse observations are calculated; conflicting NAVs stay unavailable', () => {
  const result = calculateCategoryComparison(rows, [metadata], '2026-10-03');
  assert.equal(result.funds[0]!.returns.r1m?.value, -1);
  assert.equal(result.funds[0]!.volatility.r1m?.value, 224.5);
  assert.equal(result.funds[0]!.netAumCr, null);
  assert.equal(result.funds[0]!.riskLevel, null);
  assert.equal(result.funds[0]!.returnProfile, null);
  assert.equal(result.funds[0]!.latestNav, 99);
  assert.equal(result.funds[0]!.id, 1);
  const invalid = calculateCategoryComparison([...rows, { ...rows[0]!, nav: '101' }], [metadata], '2026-10-03');
  assert.equal(invalid.funds[0]!.returns.si?.value, null);
  assert.equal(invalid.funds[0]!.volatility.si?.value, null);
  const sparse = rows.map((row, i) => ({ ...row, date: `2026-0${i + 1}-01` }));
  const metric = calculateUnifiedPerformance(sparse, '2026-03-01').funds[0]!.returns.si;
  assert.equal(annualizedVolatility(sparse, metric).status, 'complete');
});

test('risk levels use only explicit published levels and unknown categories are retained', () => {
  const result = calculateCategoryComparison(rows, [{ ...metadata, riskBand: 'Very High', strategy: null }], '2026-10-03');
  assert.equal(result.funds[0]!.riskLevel, null);
  assert.equal(result.funds[0]!.categoryId, 'other');
  assert.equal(result.categories.find((item) => item.id === 'other')?.fundCount, 1);
  assert.equal(calculateCategoryComparison(rows, [{ ...metadata, riskBand: 'Level 3' }], '2026-10-03').funds[0]!.riskLevel, 3);
});

test('comparison API reads the live database, validates dates, and keeps data dates', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const invalid = await app.inject('/api/analytics/category-comparison?to_date=2026-02-30');
    assert.equal(invalid.statusCode, 400);
    const response = await app.inject('/api/analytics/category-comparison?to_date=2026-11-05');
    assert.equal(response.statusCode, 200);
    const body = response.json();
    assert.equal(body.success, true);
    assert.deepEqual(body.platformCategories, ['Equity', 'Debt', 'Hybrid']);
    assert.equal(body.categories.find((category: { id: string }) => category.id === 'debt').fundCount, 0);
    assert.ok(body.funds.length > 0);
    assert.equal(new Set(body.funds.map((fund: { fundId: number }) => fund.fundId)).size, body.funds.length);
    assert.equal(body.categories.reduce((sum: number, item: { fundCount: number }) => sum + item.fundCount, 0), body.funds.length);
    for (const fund of body.funds) {
      assert.equal(fund.plan, 'Regular');
      assert.equal(fund.option, 'Growth');
      assert.ok(fund.latestDate === null || fund.latestDate <= body.asOfDate);
      assert.ok(fund.aumAsOf === null || fund.aumAsOf <= body.asOfDate);
      if (fund.volatility.si.value !== null) assert.ok(fund.volatility.si.observations >= 2);
    }
  } finally { await app.close(); }
});
