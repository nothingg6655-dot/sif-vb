import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { PreferredNavRow } from '../src/repositories/funds.js';
import {
  calculateAnnualAnalytics, calculateBenchmarkMonthlyReturns, calculateMonthlyAnalytics,
  calculateRollingAnalytics,
  calculateUnifiedPerformance, compareReturnMetrics, type ReturnMetric,
} from '../src/services/analytics.js';

function rows(observations: Array<[string, string]>): PreferredNavRow[] {
  return observations.map(([date, nav]) => ({
    displayOrder: 1, fundId: 101, fullName: 'Test Fund', slug: 'test-fund',
    source: 'TEST', schemeCode: 13, date, nav,
  }));
}

test('unified engine calculates every trailing period with explicit metadata', () => {
  const result = calculateUnifiedPerformance(rows([
    ['2020-10-14', '100'], ['2021-10-14', '110'], ['2023-10-14', '130'],
    ['2024-10-14', '140'], ['2025-10-14', '150'], ['2026-01-01', '155'],
    ['2026-04-14', '160'], ['2026-07-16', '170'], ['2026-09-14', '180'],
    ['2026-10-07', '190'], ['2026-10-13', '195'], ['2026-10-14', '200'],
  ]), '2026-10-14');
  const metrics = result.funds[0]!.returns;
  assert.deepEqual(Object.fromEntries(Object.entries(metrics).map(([key, value]) =>
    [key, value.value])), {
    r1d: 2.56, r1w: 5.26, r1m: 5.26, r3m: 17.65, r6m: 17.65,
    r1y: 29.03, r2y: 42.86, r3y: 53.85, r5y: 81.82,
    ytd: 29.03, si: 100,
  });
  assert.equal(metrics.r1m.startDate, '2026-10-07');
  assert.equal(metrics.r1m.endDate, '2026-10-14');
  assert.equal(metrics.r1m.actualElapsedDays, 7);
  assert.equal(metrics.r1m.calculationMethod, 'calendar-months:first-strictly-after');
});

test('calendar periods use available history while unsupported long periods require full history', () => {
  const fund = calculateUnifiedPerformance(rows([
    ['2026-10-05', '10'], ['2026-10-13', '10.1'],
  ]), '2026-10-14').funds[0]!;
  assert.equal(fund.returns.r1d.value, 1);
  assert.equal(fund.returns.r1m.value, 1);
  assert.equal(fund.returns.r5y.value, null);
  assert.equal(fund.returns.ytd.value, null);
  assert.equal(fund.returns.r1m.status, 'complete');
  assert.equal(fund.returns.si.value, 1);
});

test('monthly analytics uses nearest calendar boundaries and marks current month partial', () => {
  const result = calculateMonthlyAnalytics(rows([
    ['2026-03-31', '100'], ['2026-04-01', '110'], ['2026-04-30', '121'],
    ['2026-05-04', '133.1'], ['2026-05-31', '146.41'],
    ['2026-06-01', '150'], ['2026-06-15', '165'],
  ]), '12m', '2026-06-15');
  assert.equal(result.monthlyMap['1']!["Apr '26"], 10);
  assert.equal(result.monthlyMap['1']!["May '26"], 21);
  assert.equal(result.monthlyMap['1']!["Jun '26"], 10);
  assert.equal(result.cellMetadata['1']!["Apr '26"]!.startDate, '2026-04-01');
  assert.equal(result.cellMetadata['1']!["May '26"]!.startDate, '2026-04-30');
  assert.equal(result.cellMetadata['1']!["Jun '26"]!.status, 'partial');
  assert.equal(result.months.length, 12);
});

test('benchmark monthly returns use previous and current month-end closes', () => {
  const result = calculateBenchmarkMonthlyReturns([
    { date: '2026-03-31', value: '100' },
    { date: '2026-04-01', value: '150' },
    { date: '2026-04-30', value: '110' },
    { date: '2026-05-29', value: '121' },
    { date: '2026-06-01', value: '130' },
    { date: '2026-06-12', value: '133.1' },
  ], [
    { label: "Apr '26", fromDate: '2026-04-01', toDate: '2026-04-30', completed: true },
    { label: "May '26", fromDate: '2026-05-01', toDate: '2026-05-31', completed: true },
    { label: "Jun '26", fromDate: '2026-06-01', toDate: '2026-06-30', completed: false },
  ], '2026-06-15');
  assert.deepEqual(result.values, { "Apr '26": 10, "May '26": 10, "Jun '26": 10 });
  assert.equal(result.metadata["Apr '26"]!.startDate, '2026-03-31');
  assert.equal(result.metadata["May '26"]!.endDate, '2026-05-29');
  assert.equal(result.metadata["Jun '26"]!.status, 'partial');
});

test('calendar-year, YTD and Indian financial-year boundaries stay distinct', () => {
  const result = calculateAnnualAnalytics(rows([
    ['2025-01-01', '100'], ['2025-12-31', '110'], ['2026-01-01', '120'],
    ['2026-03-31', '130'], ['2026-04-01', '140'], ['2026-10-14', '150'],
  ]), '2026-10-14');
  assert.equal(result.calendarYearMap['1']!['2025'], 10);
  assert.equal(result.calendarYearMap['1']!['2026'], null);
  assert.equal(result.ytdMap['1']!.value, 25);
  assert.equal(result.financialYearMap['1']!['FY 2025-26'], 30);
  assert.equal(result.financialYearMap['1']!['FY 2026-27'], null);
  assert.equal(result.financialYtdMap['1']!.value, 7.14);
  assert.equal(result.financialYtdMap['1']!.status, 'ongoing');
});

test('rolling returns provide deterministic series and summary statistics', () => {
  const input = rows([
    ['2026-01-01', '100'], ['2026-01-31', '110'], ['2026-03-02', '99'],
    ['2026-04-01', '108.9'],
  ]);
  const first = calculateRollingAnalytics(input, '1m', '2026-04-01')!;
  const second = calculateRollingAnalytics([...input].reverse(), '1m', '2026-04-01')!;
  assert.deepEqual(first, second);
  assert.deepEqual(first.series.map((point) => point.value), [10, -10, 10]);
  assert.deepEqual(first.summary, {
    average: 3.33, minimum: -10, maximum: 10, median: 10, validObservations: 3,
  });
});

test('benchmark comparison distinguishes excess points and compounded relative return', () => {
  const base = (value: number): ReturnMetric => ({
    value, requestedStartDate: '2026-01-01', startDate: '2026-01-01', startValue: 100,
    endDate: '2026-12-31', endValue: 100 + value, actualElapsedDays: 364,
    calculationMethod: 'test', status: 'complete', warnings: [],
  });
  assert.deepEqual(compareReturnMetrics(base(20), base(10)), {
    fundReturn: 20, benchmarkReturn: 10, excessReturnPctPoints: 10,
    relativeReturnPct: 9.09, status: 'complete',
  });
  assert.equal(compareReturnMetrics(base(20), { ...base(10), value: null }).status, 'unavailable');
  assert.equal(compareReturnMetrics(base(20), base(-100)).status, 'invalid');
});
