import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createPool } from '../config/database.js';
import { readConfig } from '../config/env.js';
import { readPerformanceNav } from '../repositories/funds.js';
import {
  calculateAnnualAnalytics, calculateUnifiedPerformance, trailingPeriodDefinitions,
  type ReturnMetric, type TrailingPeriodId,
} from '../services/analytics.js';
import {
  absoluteReturn, calculateHeatmap, groupSeries, lastAtOrBefore,
} from '../services/performance.js';

const backendRoot = fileURLToPath(new URL('../../', import.meta.url));
const reportRoot = fileURLToPath(new URL('../../../docs/reports/', import.meta.url));

function fixture<T>(name: string): T {
  return JSON.parse(readFileSync(`${backendRoot}tests/fixtures/${name}`, 'utf8')) as T;
}

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function writeCsv(path: string, rows: Array<Record<string, unknown>>) {
  if (!rows[0]) return;
  const columns = Object.keys(rows[0]);
  writeFileSync(path, `${[
    columns.join(','),
    ...rows.map((row) => columns.map((column) => csvCell(row[column])).join(',')),
  ].join('\n')}\n`);
}

function same(left: number | null | undefined, right: number | null | undefined): boolean {
  return left === right || (typeof left === 'number' && typeof right === 'number'
    && Math.abs(left - right) < 1e-12);
}

function minDate(left: string, right: string): string { return left < right ? left : right; }

interface LiveFixture { data: Array<{ fullName: string; latestNAV: number; change: number }> }
interface PerformanceFixture {
  performanceMap: Record<string, {
    changePct: number | null; return1m: number | null; return3m: number | null;
  }>;
}
interface HeatmapFixture {
  monthlyMap: Record<string, Record<string, number | null>>;
  months: Array<{ label: string; fromDate: string; toDate: string }>;
}
interface BenchmarkFixture {
  benchmarks: Array<{ id: string; metrics: Record<string, string | number | null> }>;
}

async function main() {
  mkdirSync(reportRoot, { recursive: true });
  const pool = createPool(readConfig());
  try {
    const rows14 = await readPerformanceNav(pool, '2026-10-14');
    const unified = calculateUnifiedPerformance(rows14, '2026-10-14');
    const live = fixture<LiveFixture>('sif360-live-ticker-2026-10-14.json');
    const performance = fixture<PerformanceFixture>('sif360-performance-2026-10-14.json');
    const periodReference = (id: number, period: TrailingPeriodId): number | null | undefined => {
      if (period === 'r1d') return live.data[id - 1]?.change;
      if (period === 'r1m') return performance.performanceMap[String(id)]?.return1m;
      if (period === 'r3m') return performance.performanceMap[String(id)]?.return3m;
      if (period === 'si') return performance.performanceMap[String(id)]?.changePct;
      return undefined;
    };
    const periodRows: Array<Record<string, unknown>> = [];
    for (const fund of unified.funds) {
      for (const definition of trailingPeriodDefinitions) {
        const calculated = fund.returns[definition.id];
        const reference = periodReference(fund.id, definition.id);
        const status = reference === undefined ? 'missing-reference'
          : same(calculated.value, reference) ? 'exact-match'
          : calculated.value === null ? 'insufficient-historical-data' : 'mismatch';
        periodRows.push({
          fundId: fund.id, fullName: fund.fullName, period: definition.id,
          referenceValue: reference, calculatedValue: calculated.value,
          difference: typeof reference === 'number' && calculated.value !== null
            ? Math.round((calculated.value - reference) * 100) / 100 : null,
          requestedStartDate: calculated.requestedStartDate,
          startDate: calculated.startDate, startValue: calculated.startValue,
          endDate: calculated.endDate, endValue: calculated.endValue,
          actualElapsedDays: calculated.actualElapsedDays,
          calculationMethod: calculated.calculationMethod,
          calculationStatus: calculated.status,
          matchStatus: status,
          warnings: calculated.warnings.join('|'),
        });
      }
    }

    const rows08 = await readPerformanceNav(pool, '2026-10-08');
    const grouped08 = new Map(groupSeries(rows08, '2026-10-08')
      .map((fund) => [String(fund.displayOrder), fund]));
    const monthlyRows: Array<Record<string, unknown>> = [];
    for (const window of ['3m', '6m', '12m', 'all'] as const) {
      const expected = fixture<HeatmapFixture>(`sif360-heatmap-${window}-2026-10-08.json`);
      const actual = calculateHeatmap(rows08, window, '2026-10-08');
      for (const [fundId, referenceMonths] of Object.entries(expected.monthlyMap)) {
        const fund = grouped08.get(fundId);
        for (const period of expected.months) {
          const reference = referenceMonths[period.label] ?? null;
          const calculated = actual.monthlyMap[fundId]?.[period.label] ?? null;
          const endBoundary = minDate(period.toDate, '2026-10-08');
          const start = fund ? lastAtOrBefore(fund.observations, period.fromDate) : undefined;
          const end = fund ? lastAtOrBefore(fund.observations, endBoundary) : undefined;
          const value = start && end && start.date < end.date ? absoluteReturn(start, end) : null;
          const snapshotDifference = period.label === "Oct '26" && !same(reference, calculated);
          monthlyRows.push({
            window, fundId: Number(fundId), period: period.label,
            referenceValue: reference, calculatedValue: calculated,
            difference: typeof reference === 'number' && typeof calculated === 'number'
              ? Math.round((calculated - reference) * 100) / 100 : null,
            startDate: start?.date ?? null,
            startValue: start ? Number(start.nav) : null,
            endDate: end?.date ?? null,
            endValue: end ? Number(end.nav) : null,
            independentlyCalculatedValue: value,
            matchStatus: same(reference, calculated) ? 'exact-match'
              : snapshotDifference ? 'source-snapshot-difference' :
                calculated === null ? 'insufficient-historical-data' : 'mismatch',
          });
        }
      }
    }

    const benchmark = fixture<BenchmarkFixture>('sif360-benchmark-performance-2026-10-08.json');
    const benchmarkRows = benchmark.benchmarks.flatMap((item) =>
      Object.entries(item.metrics).map(([period, referenceValue]) => ({
        benchmarkId: item.id, period, referenceValue, calculatedValue: null,
        matchStatus: referenceValue === null ? 'exact-null' : 'verified-source-unavailable',
        reason: 'Reference output has no auditable historical index-level series or PRICE/TRI declaration',
      })));

    const annual = calculateAnnualAnalytics(rows14, '2026-10-14');
    const summary = {
      generatedAt: new Date().toISOString(),
      sourceCoverage: { firstDate: '2025-10-08', latestDate: '2026-10-13' },
      periodCoverage: Object.fromEntries(trailingPeriodDefinitions.map(({ id }) => [id, {
        calculated: unified.funds.filter((fund) => fund.returns[id].value !== null).length,
        unavailable: unified.funds.filter((fund) => fund.returns[id].value === null).length,
      }])),
      periodReconciliation: {
        exact: periodRows.filter((row) => row.matchStatus === 'exact-match').length,
        mismatch: periodRows.filter((row) => row.matchStatus === 'mismatch').length,
        missingReference: periodRows.filter((row) => row.matchStatus === 'missing-reference').length,
        insufficientHistory: periodRows.filter((row) =>
          row.matchStatus === 'insufficient-historical-data').length,
      },
      monthlyReconciliation: {
        cells: monthlyRows.length,
        exact: monthlyRows.filter((row) => row.matchStatus === 'exact-match').length,
        referenceNonNull: monthlyRows.filter((row) => row.referenceValue !== null).length,
        exactNonNull: monthlyRows.filter((row) => row.matchStatus === 'exact-match'
          && row.referenceValue !== null).length,
        mismatch: monthlyRows.filter((row) => row.matchStatus === 'mismatch').length,
        sourceSnapshotDifference: monthlyRows.filter((row) =>
          row.matchStatus === 'source-snapshot-difference').length,
        insufficientHistory: monthlyRows.filter((row) =>
          row.matchStatus === 'insufficient-historical-data').length,
      },
      annualCoverage: {
        calendarPeriods: annual.calendarPeriods,
        financialPeriods: annual.financialPeriods,
        completedCalendarValues: Object.values(annual.calendarYearMap)
          .flatMap((value) => Object.values(value)).filter((value) => value !== null).length,
        completedFinancialYearValues: Object.values(annual.financialYearMap)
          .flatMap((value) => Object.values(value)).filter((value) => value !== null).length,
      },
      benchmarkCoverage: {
        configured: benchmark.benchmarks.length, verifiedHistoricalSeries: 0,
        comparisonStatus: 'unavailable',
      },
    };
    const jsonPath = `${reportRoot}sif-analytics-reconciliation.json`;
    writeFileSync(jsonPath, `${JSON.stringify({ summary, periodRows, monthlyRows, benchmarkRows }, null, 2)}\n`);
    writeCsv(`${reportRoot}sif-analytics-period-reconciliation.csv`, periodRows);
    writeCsv(`${reportRoot}sif360-monthly-reconciliation.csv`, monthlyRows);
    writeCsv(`${reportRoot}sif360-benchmark-reconciliation.csv`, benchmarkRows);
    process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
  } finally {
    await pool.end();
  }
}

main().catch((error: unknown) => {
  process.stderr.write(`Analytics reconciliation failed: ${
    error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
