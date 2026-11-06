import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { buildApp } from '../src/app.js';
import { readConfig } from '../src/config/env.js';

const sif360Reference = JSON.parse(readFileSync(
  new URL('./fixtures/sif360-performance-2026-10-14.json', import.meta.url), 'utf8'
));
const heatmapReference = JSON.parse(readFileSync(
  new URL('./fixtures/sif360-heatmap-12m-2026-10-14.json', import.meta.url), 'utf8'
));
const benchmarkReference = JSON.parse(readFileSync(
  new URL('./fixtures/sif360-benchmark-performance-2026-10-08.json', import.meta.url), 'utf8'
));

test('live ticker returns configured funds with numeric NAV and daily change', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const response = await app.inject({ method: 'GET', url: '/api/liveTicker' });
    assert.equal(response.statusCode, 200);
    const body = response.json();
    assert.equal(body.data.length, 34);
    assert.equal(typeof body.data[0].fullName, 'string');
    assert.equal(typeof body.data[0].latestNAV, 'number');
    assert.ok(body.data[0].change === null || typeof body.data[0].change === 'number');
    assert.equal(body.data[0].fullName, 'Magnum Hybrid Long-Short Fund');
  } finally { await app.close(); }
});

test('live ticker preserves configured order and remains deterministic after source refreshes', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const response = await app.inject({ method: 'GET', url: '/api/liveTicker' });
    assert.equal(response.statusCode, 200);
    const actual = response.json().data;
    const repeated = await app.inject({ method: 'GET', url: '/api/liveTicker' });
    assert.deepEqual(repeated.json().data, actual);
    assert.equal(actual.length, 34);
    const names = actual.map((item: { fullName: string }) => item.fullName);
    assert.equal(new Set(names).size, 34);
    assert.ok(names.every((name: string) => name.length > 0));
  } finally { await app.close(); }
});

test('NAV history validates parameters and paginates ascending observations', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const response = await app.inject({
      method: 'GET',
      url: '/api/nav-history?sd_id=SIF-13&from_date=2025-10-22&to_date=2026-10-08'
    });
    assert.equal(response.statusCode, 200);
    const body = response.json();
    assert.equal(body.success, true);
    assert.deepEqual(Object.keys(body), ['success', 'data']);
    assert.ok(body.data.length > 10);
    assert.ok(body.data[0].date < body.data[9].date);
    assert.equal(typeof body.data[0].nav, 'number');

    const paginated = await app.inject({
      method: 'GET',
      url: '/api/nav-history?sd_id=SIF-13&from_date=2025-10-22&to_date=2026-10-08&limit=10&page=1'
    });
    assert.equal(paginated.json().data.length, 10);
    assert.ok(paginated.json().pagination.total > 10);

    const invalid = await app.inject({
      method: 'GET', url: '/api/nav-history?sd_id=bad&from_date=2026-10-10&to_date=2026-01-01'
    });
    assert.equal(invalid.statusCode, 400);
    assert.equal(invalid.json().error.code, 'INVALID_QUERY');
  } finally { await app.close(); }
});

test('heatmap and performance preserve keyed response style and methodology', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const heatmap = await app.inject({
      method: 'GET', url: '/api/heatmap-all?window=6m&to_date=2026-10-08'
    });
    assert.equal(heatmap.statusCode, 200);
    const heatmapBody = heatmap.json();
    assert.equal(heatmapBody.success, true);
    assert.deepEqual(Object.keys(heatmapBody),
      ['success', 'monthlyMap', 'months', 'benchmarkMap', 'benchmarks']);
    assert.equal(Object.keys(heatmapBody.monthlyMap).length, 34);
    assert.equal(heatmapBody.months.length, 7);
    assert.deepEqual(Object.keys(heatmapBody.benchmarkMap),
      ['bse500', 'nifty50', 'niftymidsml']);
    assert.equal(heatmapBody.benchmarks.length, 3);

    const performance = await app.inject({
      method: 'GET',
      url: '/api/performance-all?to_date=2026-10-08&from1m=2026-09-08&from3m=2026-07-10'
    });
    assert.equal(performance.statusCode, 200);
    const performanceBody = performance.json();
    assert.equal(performanceBody.success, true);
    assert.equal(Object.keys(performanceBody.performanceMap).length, 34);
    const first = performanceBody.performanceMap['1'];
    assert.deepEqual(Object.keys(first), [
      'changePct', 'return1m', 'return3m', 'latestNav', 'latestDate', 'inceptionDate'
    ]);
    assert.equal(typeof first.latestNav, 'number');

    const invalid = await app.inject({
      method: 'GET', url: '/api/heatmap-all?window=2m&to_date=2026-10-08'
    });
    assert.equal(invalid.statusCode, 400);
  } finally { await app.close(); }
});

test('performance reconciles all funds at the reference source cutoff', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const response = await app.inject({
      method: 'GET',
      url: '/api/performance-all?to_date=2026-10-13&from1m=2026-09-14&from3m=2026-07-16',
    });
    assert.equal(response.statusCode, 200);
    const actual = response.json();
    assert.deepEqual(Object.keys(actual.performanceMap),
      Array.from({ length: 34 }, (_, index) => String(index + 1)));
    for (const referenceId of Object.keys(sif360Reference.performanceMap)) {
      assert.deepEqual(actual.performanceMap[referenceId],
        sif360Reference.performanceMap[referenceId], `reference fund ${referenceId}`);
    }
  } finally { await app.close(); }
});

test('heatmap reconciles every source-supported numeric cell in the current SIF360 response', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const response = await app.inject({
      method: 'GET', url: '/api/heatmap-all?window=12m&to_date=2026-10-13',
    });
    assert.equal(response.statusCode, 200);
    const actual = response.json();
    let compared = 0;
    for (const [fundId, expectedMonths] of Object.entries(
      heatmapReference.monthlyMap as Record<string, Record<string, number | null>>,
    )) {
      for (const [month, expected] of Object.entries(expectedMonths)) {
        if (expected === null) continue;
        assert.equal(actual.monthlyMap[fundId][month], expected, `${fundId} ${month}`);
        compared += 1;
      }
    }
    assert.equal(compared, 206);
    let benchmarkCells = 0;
    for (const [benchmarkId, expectedMonths] of Object.entries(
      heatmapReference.benchmarkMap as Record<string, Record<string, number>>,
    )) {
      for (const [month, expected] of Object.entries(expectedMonths)) {
        if (month === "Oct '26" && ['bse500', 'nifty50'].includes(benchmarkId)) continue;
        assert.equal(actual.benchmarkMap[benchmarkId][month], expected,
          `${benchmarkId} ${month}`);
        benchmarkCells += 1;
      }
    }
    assert.equal(benchmarkCells, 37);
  } finally { await app.close(); }
});

test('normalized analytics APIs expose periods, heatmaps, annual and rolling data', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const performance = await app.inject({
      method: 'GET', url: '/api/analytics/performance?to_date=2026-10-13',
    });
    assert.equal(performance.statusCode, 200);
    const performanceBody = performance.json();
    assert.equal(performanceBody.funds.length, 34);
    assert.deepEqual(performanceBody.periods.map((period: { id: string }) => period.id),
      ['r1d', 'r1w', 'r1m', 'r3m', 'r6m', 'r1y', 'r2y', 'r3y', 'r5y', 'ytd', 'si']);
    assert.equal(performanceBody.funds[0].returns.r1d.value, 0.21);
    assert.equal(performanceBody.funds[0].returns.r2y.value, null);
    assert.equal(performanceBody.funds[33].returns.r1m.value, null);

    const monthly = await app.inject({
      method: 'GET', url: '/api/analytics/monthly-heatmap?range=12m&to_date=2026-10-13',
    });
    assert.equal(monthly.statusCode, 200);
    assert.equal(monthly.json().funds.length, 34);
    assert.equal(monthly.json().months.length, 12);
    assert.equal(monthly.json().months.at(-1).status, 'partial');
    assert.equal(monthly.json().benchmarkMap.bse500[monthly.json().months[0].label], 0.86);

    const annual = await app.inject({
      method: 'GET', url: '/api/analytics/annual?to_date=2026-10-13',
    });
    assert.equal(annual.statusCode, 200);
    assert.equal(annual.json().calendarPeriods.at(-1).completed, false);
    assert.equal(Object.keys(annual.json().calendarYearMap).length, 34);

    const rolling = await app.inject({
      method: 'GET', url: '/api/analytics/rolling?fund_id=1&window=1m&to_date=2026-10-13',
    });
    assert.equal(rolling.statusCode, 200);
    assert.ok(rolling.json().summary.validObservations > 0);
    assert.ok(rolling.json().series.length > 0);

    const trend = await app.inject({
      method: 'GET',
      url: '/api/analytics/trend?fund_id=1&from_date=2026-09-01&to_date=2026-10-13',
    });
    assert.equal(trend.statusCode, 200);
    assert.ok(trend.json().series.length > 1);
    assert.equal(trend.json().series[0].cumulativeReturnPct, 0);
  } finally { await app.close(); }
});

test('benchmark APIs expose official PRICE-index coverage and calculated returns', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const catalog = await app.inject({
      method: 'GET', url: '/api/analytics/benchmarks?to_date=2026-10-14',
    });
    assert.equal(catalog.statusCode, 200);
    assert.equal(catalog.json().benchmarks.length, 3);
    assert.equal(catalog.json().benchmarks[0].coverageStatus, 'available');
    assert.equal(catalog.json().benchmarks[0].indexVariant, 'PRICE');
    assert.equal(catalog.json().benchmarks[0].source, 'BSE Indices');
    assert.equal(catalog.json().benchmarks[0].returns.r1m.value, -1.95);

    const comparison = await app.inject({
      method: 'GET',
      url: '/api/analytics/benchmark-comparison?fund_id=1&benchmark_id=bse500&to_date=2026-10-14',
    });
    assert.equal(comparison.statusCode, 200);
    assert.equal(comparison.json().periods.r1m.fundReturn, -1.18);
    assert.equal(comparison.json().periods.r1m.benchmarkReturn, -1.95);
    assert.equal(comparison.json().periods.r1m.excessReturnPctPoints, 0.77);
  } finally { await app.close(); }
});

test('benchmark and TER compatibility routes preserve response shape', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const benchmark = await app.inject({
      method: 'GET', url: '/api/benchmark-performance?to_date=2026-10-08',
    });
    assert.equal(benchmark.statusCode, 200);
    assert.equal(benchmark.json().success, true);
    assert.equal(benchmark.json().benchmarks.length, 3);
    const benchmarkBody = benchmark.json();
    assert.deepEqual(benchmarkBody.benchmarks.map((item: { id: string }) => item.id),
      benchmarkReference.benchmarks.map((item: { id: string }) => item.id));
    for (const [index, item] of benchmarkBody.benchmarks.entries()) {
      assert.deepEqual(Object.keys(item.metrics),
        Object.keys(benchmarkReference.benchmarks[index].metrics));
      assert.equal(typeof item.metrics.r1d, 'number');
      assert.equal(typeof item.metrics.latestNav, 'string');
      assert.equal(item.metrics.si, null);
      assert.ok(item.metrics.latestDate <= '2026-10-08');
    }
    assert.deepEqual(benchmarkBody.benchmarks[1], benchmarkReference.benchmarks[1]);
    const ter = await app.inject({ method: 'GET', url: '/api/sif-ter' });
    assert.equal(ter.statusCode, 200);
    assert.equal(ter.json().success, true);
    assert.equal(ter.json().count, 0);
    assert.deepEqual(ter.json().funds, []);
  } finally { await app.close(); }
});

test('fund details expose variants and source data without invented factsheet values', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const response = await app.inject({
      method: 'GET', url: '/api/funds/magnum-hybrid-long-short-fund'
    });
    assert.equal(response.statusCode, 200);
    const body = response.json().data;
    assert.equal(body.fullName, 'Magnum Hybrid Long-Short Fund');
    assert.equal(body.schemes.length, 2);
    assert.ok(body.schemes.every((scheme: { plan: string }) => scheme.plan === 'Regular'));
    assert.ok(body.quarterlyAAUM.length > 0);
    assert.match(body.factsheet, /^https:\/\//);
    assert.equal(body.benchmark.available, false);
    assert.equal(body.expenseRatio.available, true);
    assert.equal(body.expenseRatio.type, 'BASE');
    assert.equal(typeof body.expenseRatio.percent, 'number');

    const missing = await app.inject({ method: 'GET', url: '/api/funds/not-a-real-fund' });
    assert.equal(missing.statusCode, 404);
  } finally { await app.close(); }
});

test('non-loopback server configuration requires an admin API key', () => {
  assert.throws(
    () => readConfig({ ...process.env, HOST: '0.0.0.0', ADMIN_API_KEY: '' }),
    /ADMIN_API_KEY/
  );
  assert.doesNotThrow(
    () => readConfig({ ...process.env, HOST: '0.0.0.0', ADMIN_API_KEY: 'test-secret' })
  );
});
