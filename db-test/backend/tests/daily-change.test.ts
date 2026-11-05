import assert from 'node:assert/strict';
import { test } from 'node:test';
import { calculateDailyTicker, type DailyNavRow } from '../src/services/daily-change.js';

function row(
  date: string, nav: string, overrides: Partial<DailyNavRow> = {},
): DailyNavRow {
  return {
    displayOrder: 1,
    fundId: 101,
    fullName: 'Example Long-Short Fund',
    source: 'TEST',
    schemeCode: 13,
    plan: 'Regular',
    option: 'Growth',
    date,
    nav,
    ...overrides,
  };
}

test('daily change handles positive, negative, zero, high-NAV and half-up rounding', () => {
  assert.equal(calculateDailyTicker([
    row('2026-10-12', '10.2803'), row('2026-10-13', '10.3021'),
  ]).data[0]?.change, 0.21);
  assert.equal(calculateDailyTicker([
    row('2026-10-12', '10.00'), row('2026-10-13', '9.90'),
  ]).data[0]?.change, -1);
  assert.equal(calculateDailyTicker([
    row('2026-10-12', '10.00'), row('2026-10-13', '10.00'),
  ]).data[0]?.change, 0);
  assert.equal(calculateDailyTicker([
    row('2026-10-12', '862.3983'), row('2026-10-13', '870.2380'),
  ]).data[0]?.change, 0.91);
  assert.equal(calculateDailyTicker([
    row('2026-10-12', '32'), row('2026-10-13', '32.08'),
  ]).data[0]?.change, 0.25);
});

test('previous observation is the immediately preceding available date', () => {
  const result = calculateDailyTicker([
    row('2026-10-09', '10.00'), row('2026-10-13', '10.10'),
  ]);
  assert.equal(result.data[0]?.change, 1);
  assert.equal(result.diagnostics[0]?.previousDate, '2026-10-09');
});

test('missing or invalid previous NAV returns null with a warning', () => {
  const missing = calculateDailyTicker([row('2026-10-13', '10')]);
  assert.equal(missing.data[0]?.change, null);
  assert.ok(missing.diagnostics[0]?.dataQualityWarnings.includes('previous_observation_missing'));

  const invalid = calculateDailyTicker([
    row('2026-10-12', '0'), row('2026-10-13', '10'),
  ]);
  assert.equal(invalid.data[0]?.change, null);
  assert.ok(invalid.diagnostics[0]?.dataQualityWarnings.includes('previous_nav_invalid'));
});

test('identical duplicates are deterministic and conflicting duplicates are rejected', () => {
  const duplicates = [
    row('2026-10-12', '10'),
    row('2026-10-12', '10.0000'),
    row('2026-10-13', '10.10'),
  ];
  const first = calculateDailyTicker(duplicates);
  const second = calculateDailyTicker([...duplicates].reverse());
  assert.deepEqual(first, second);
  assert.equal(first.data[0]?.change, 1);
  assert.ok(first.diagnostics[0]?.dataQualityWarnings
    .includes('duplicate_observation_deduplicated'));

  const conflict = calculateDailyTicker([
    row('2026-10-12', '10'),
    row('2026-10-13', '10.10'),
    row('2026-10-13', '10.20'),
  ]);
  assert.equal(conflict.data[0]?.latestNAV, null);
  assert.equal(conflict.data[0]?.change, null);
  assert.equal(conflict.diagnostics[0]?.calculationStatus, 'invalid');
  assert.ok(conflict.diagnostics[0]?.dataQualityWarnings
    .includes('latest_observation_conflicting'));
});

test('similar names, different schemes, plans and options remain isolated', () => {
  const result = calculateDailyTicker([
    row('2026-10-12', '10', { displayOrder: 1, schemeCode: 13, fullName: 'Alpha Fund' }),
    row('2026-10-13', '11', { displayOrder: 1, schemeCode: 13, fullName: 'Alpha Fund' }),
    row('2026-10-12', '20', {
      displayOrder: 2, fundId: 102, schemeCode: 14, fullName: 'Alpha Fund Regular',
      plan: 'Direct', option: 'Growth',
    }),
    row('2026-10-13', '18', {
      displayOrder: 2, fundId: 102, schemeCode: 14, fullName: 'Alpha Fund Regular',
      plan: 'Direct', option: 'Growth',
    }),
  ]);
  assert.deepEqual(result.data.map((item) => item.change), [10, -10]);
  assert.deepEqual(result.diagnostics.map((item) => item.schemeCode), [13, 14]);
  assert.deepEqual(result.diagnostics.map((item) => item.plan), ['Regular', 'Direct']);
});

test('mixing multiple scheme series under one configured fund is rejected', () => {
  const result = calculateDailyTicker([
    row('2026-10-12', '10'), row('2026-10-13', '11'),
    row('2026-10-12', '20', { schemeCode: 14, plan: 'Direct' }),
    row('2026-10-13', '21', { schemeCode: 14, plan: 'Direct' }),
  ]);
  assert.equal(result.data[0]?.change, null);
  assert.ok(result.diagnostics[0]?.dataQualityWarnings.includes('multiple_scheme_series'));
});
