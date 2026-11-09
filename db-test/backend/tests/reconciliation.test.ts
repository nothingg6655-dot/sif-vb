import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { createPool } from '../src/config/database.js';
import { readConfig } from '../src/config/env.js';
import { readPerformanceNav, type PreferredNavRow } from '../src/repositories/funds.js';
import {
  calculateSchemePerformance, percentageReturn, SIF360_OBSERVED_POLICY,
  type PerformanceCalculationPolicy,
} from '../src/services/performance.js';

const reference = JSON.parse(readFileSync(
  new URL('./fixtures/sif360-performance-2026-10-14.json', import.meta.url), 'utf8'
)).performanceMap;

function groups(rows: PreferredNavRow[]) {
  const result = new Map<number, PreferredNavRow[]>();
  for (const row of rows) {
    const items = result.get(row.displayOrder) ?? [];
    items.push(row);
    result.set(row.displayOrder, items);
  }
  return result;
}

function calculate(
  rows: PreferredNavRow[], target1m: string, target3m: string,
  oneMonthSelection: PerformanceCalculationPolicy['oneMonthSelection'],
  threeMonthSelection: PerformanceCalculationPolicy['threeMonthSelection'],
) {
  const first = rows[0]!;
  return calculateSchemePerformance({
    source: first.source, schemeCode: first.schemeCode,
    observations: rows.map(({ date, nav }) => ({ date, nav })),
  }, '2026-10-13', target1m, target3m, {
    ...SIF360_OBSERVED_POLICY, oneMonthSelection, threeMonthSelection,
  });
}

test('all candidate boundary and fixed-observation methods have measured match rates', async () => {
  const pool = createPool(readConfig());
  try {
    const grouped = groups(await readPerformanceNav(pool, '2026-10-13'));
    assert.equal(grouped.size, 34);
    const matchRate = (
      metric: 'return1m' | 'return3m', target1m: string, target3m: string,
      one: PerformanceCalculationPolicy['oneMonthSelection'],
      three: PerformanceCalculationPolicy['threeMonthSelection'],
    ) => [...grouped].filter(([id, rows]) =>
      calculate(rows, target1m, target3m, one, three)[metric] === reference[String(id)][metric]
    ).length;

    const rates = {
      oneMonthBefore: matchRate('return1m', '2026-09-14', '2026-07-16', 'on-or-before', 'on-or-after'),
      oneMonthAfter: matchRate('return1m', '2026-09-14', '2026-07-16', 'on-or-after', 'on-or-after'),
      oneMonthStrictAfter: matchRate('return1m', '2026-09-14', '2026-07-16', 'strictly-after', 'on-or-after'),
      calendarThreeMonthAfter: matchRate('return3m', '2026-09-14', '2026-07-14', 'on-or-after', 'on-or-after'),
      ninetyDayBefore: matchRate('return3m', '2026-09-14', '2026-07-16', 'on-or-after', 'on-or-before'),
      ninetyDayAfter: matchRate('return3m', '2026-09-14', '2026-07-16', 'on-or-after', 'on-or-after'),
      ninetyDayStrictAfter: matchRate('return3m', '2026-09-14', '2026-07-16', 'on-or-after', 'strictly-after'),
    };
    assert.equal(rates.oneMonthAfter, 34);
    assert.equal(rates.ninetyDayAfter, 34);
    assert.ok(rates.oneMonthAfter > rates.oneMonthBefore);
    assert.ok(rates.ninetyDayAfter > rates.calendarThreeMonthAfter);

    const bestFixed = (metric: 'return1m' | 'return3m', candidates: number[]) => {
      let best = { intervals: 0, matches: -1 };
      for (const intervals of candidates) {
        let matches = 0;
        for (const [id, rows] of grouped) {
          const ordered = [...rows].sort((left, right) => left.date.localeCompare(right.date));
          const latest = ordered.at(-1)!;
          const start = ordered[Math.max(0, ordered.length - 1 - intervals)]!;
          if (percentageReturn(start.nav, latest.nav, true).value === reference[String(id)][metric]) {
            matches += 1;
          }
        }
        if (matches > best.matches) best = { intervals, matches };
      }
      return best;
    };
    // A particular snapshot can make a fixed observation count coincide with
    // a date boundary. Such a tie does not define the production convention.
    assert.ok(bestFixed('return1m', [18,19,20,21,22,23,24]).matches <= rates.oneMonthAfter);
    assert.ok(bestFixed('return3m', [60,61,62,63,64,65,66,67,68]).matches <= rates.ninetyDayAfter);
  } finally {
    await pool.end();
  }
});
