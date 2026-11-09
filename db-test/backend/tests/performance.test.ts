import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { PreferredNavRow } from '../src/repositories/funds.js';
import {
  calculateHeatmap, calculatePerformance, calculateSchemePerformance,
  percentageReturn, selectReferenceObservation, shiftDays, shiftMonths,
} from '../src/services/performance.js';

const rows: PreferredNavRow[] = [
  ['2026-01-31', '100'], ['2026-02-28', '110'],
  ['2026-03-31', '121'], ['2026-04-15', '133.1'],
].map(([date, nav]) => ({
  displayOrder: 1, fundId: 1, fullName: 'Test Fund', slug: 'test-fund',
  source: 'TEST', schemeCode: 1, date: date!, nav: nav!,
}));

test('calendar and fixed-day shifting are UTC-stable', () => {
  assert.equal(shiftMonths('2026-03-31', 1), '2026-02-28');
  assert.equal(shiftDays('2026-10-14', 30), '2026-09-14');
  assert.equal(shiftDays('2026-10-14', 90), '2026-07-16');
});

test('percentage calculation preserves decimal precision until half-up rounding', () => {
  assert.deepEqual(percentageReturn('10.0336', '10.3021'), {
    value: 2.68, unrounded: '2.676008611066',
  });
  assert.deepEqual(percentageReturn('10.0000', '10.0000', true), {
    value: null, unrounded: '0',
  });
  assert.equal(percentageReturn('0', '10').value, null);
  assert.equal(percentageReturn('bad', '10').value, null);
});

test('observed SIF360 policy reproduces the reported fund 1 calculations', () => {
  const result = calculateSchemePerformance({
    source: 'TIGZIG', schemeCode: 13,
    observations: [
      { date: '2025-10-29', nav: '10.0336' },
      { date: '2026-07-16', nav: '10.4461' },
      { date: '2026-09-11', nav: '10.2120' },
      { date: '2026-09-15', nav: '10.3919' },
      { date: '2026-10-13', nav: '10.3021' },
    ],
  }, '2026-10-14', '2026-09-14', '2026-07-16');
  assert.deepEqual({
    changePct: result.changePct, return1m: result.return1m,
    return3m: result.return3m, latestNav: result.latestNav,
    referenceDate1m: result.referenceDate1m,
    referenceNav1m: result.referenceNav1m,
    referenceDate3m: result.referenceDate3m,
    referenceNav3m: result.referenceNav3m,
  }, {
    changePct: 2.68, return1m: -0.86, return3m: -1.38,
    latestNav: 10.3021, referenceDate1m: '2026-09-15',
    referenceNav1m: 10.3919, referenceDate3m: '2026-07-16',
    referenceNav3m: 10.4461,
  });
  assert.equal(result.calculationMethod, 'sif360-observed-30d-90d-v1');
});

test('reference selection direction is explicit around missing dates', () => {
  const observations = [
    { date: '2026-09-11', nav: '10' },
    { date: '2026-09-15', nav: '11' },
  ];
  assert.equal(selectReferenceObservation(observations, '2026-09-14', 'on-or-before')?.date,
    '2026-09-11');
  assert.equal(selectReferenceObservation(observations, '2026-09-14', 'on-or-after')?.date,
    '2026-09-15');
  assert.equal(selectReferenceObservation(observations, '2026-09-15', 'strictly-after'),
    undefined);
});

test('recent funds use inception with partial status and preserve observed null zeros', () => {
  const result = calculateSchemePerformance({
    source: 'TEST', schemeCode: 2,
    observations: [
      { date: '2026-09-01', nav: '10.01' },
      { date: '2026-09-15', nav: '9.93' },
      { date: '2026-10-13', nav: '10.01' },
    ],
  }, '2026-10-14', '2026-09-14', '2026-07-16');
  assert.equal(result.changePct, null);
  assert.equal(result.return1m, 0.81);
  assert.equal(result.return3m, null);
  assert.equal(result.referenceDate3m, '2026-09-01');
  assert.equal(result.calculationStatus, 'partial');
  assert.ok(result.dataQualityWarnings.includes('three_month_uses_inception'));
});

test('duplicates and invalid observations are handled deterministically', () => {
  const base = [
    { date: '2026-01-01', nav: '1000' },
    { date: '2026-01-01', nav: '1000.0' },
    { date: 'bad-date', nav: '20' },
    { date: '2026-04-01', nav: '956.5907' },
  ];
  const first = calculateSchemePerformance(
    { source: 'TEST', schemeCode: 3, observations: base },
    '2026-04-01', '2026-01-01', '2026-01-01',
  );
  const second = calculateSchemePerformance(
    { source: 'TEST', schemeCode: 3, observations: [...base].reverse() },
    '2026-04-01', '2026-01-01', '2026-01-01',
  );
  assert.deepEqual(first, second);
  assert.equal(first.changePct, -4.34);
  assert.ok(first.dataQualityWarnings.includes('duplicate_observation_deduplicated'));
  assert.ok(first.dataQualityWarnings.includes('invalid_observation_excluded'));

  const conflict = calculateSchemePerformance({
    source: 'TEST', schemeCode: 4,
    observations: [
      { date: '2026-01-01', nav: '10' },
      { date: '2026-01-01', nav: '11' },
      { date: '2026-02-01', nav: '12' },
    ],
  }, '2026-02-01');
  assert.equal(conflict.calculationStatus, 'invalid');
  assert.ok(conflict.dataQualityWarnings.includes('conflicting_duplicate_observation'));
});

test('historical SIF360 policy adapter retains its explicitly selected 30/90-day convention', () => {
  const result = calculatePerformance(rows, '2026-04-15');
  const item = result.performanceMap['1']!;
  assert.equal(item.return1m, 10);
  assert.equal(item.return3m, 33.1);
  assert.equal(item.changePct, 33.1);
  assert.equal(item.latestDate, '15 Apr 2026');
  assert.equal(item.inceptionDate, '31 Jan 2026');
});

test('compatibility heatmap uses nearest calendar boundaries and explicit null cells', () => {
  const result = calculateHeatmap(rows, '3m', '2026-04-15');
  assert.deepEqual(result.monthlyMap['1'], {
    "Jan '26": null, "Feb '26": 10, "Mar '26": 10, "Apr '26": 10,
  });
  assert.equal(result.months.length, 4);
  assert.deepEqual(result.months[0], {
    label: "Jan '26", fromDate: '2026-01-01', toDate: '2026-01-31',
  });
  assert.deepEqual(Object.keys(result.benchmarkMap), ['bse500', 'nifty50', 'niftymidsml']);
  assert.equal(result.benchmarks.length, 3);
});

test('heatmap chooses the closest observation at both calendar boundaries', () => {
  const boundaryRows = [
    ['2026-04-30', '10.0655'], ['2026-05-04', '10.1527'],
    ['2026-05-29', '10.2620'], ['2026-06-01', '10.1807'],
  ].map(([date, nav]) => ({
    displayOrder: 1, fundId: 1, fullName: 'Boundary Fund', slug: 'boundary-fund',
    source: 'TEST', schemeCode: 1, date: date!, nav: nav!,
  }));
  const result = calculateHeatmap(boundaryRows, '3m', '2026-06-02');
  assert.equal(result.monthlyMap['1']!["May '26"], 1.14);
});
