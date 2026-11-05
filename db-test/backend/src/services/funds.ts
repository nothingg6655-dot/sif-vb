import type pg from 'pg';
import {
  readFundDetails, readLiveTicker, readNavHistory, readPerformanceNav,
  readPreferredNav, resolveScheme
} from '../repositories/funds.js';
import {
  readAllBenchmarkHistory, readBenchmarkHistory, readBenchmarks,
} from '../repositories/benchmarks.js';
import {
  benchmarkAvailability, benchmarkDefinitions, terAvailability
} from './benchmark.js';
import { calculateDailyTicker } from './daily-change.js';
import {
  calculateHeatmap, calculatePerformance, groupSeries, percentageReturn,
} from './performance.js';
import {
  calculateAnnualAnalytics, calculateBenchmarkMonthlyReturns, calculateMonthlyAnalytics,
  calculateRollingAnalytics,
  calculateUnifiedPerformance, compareReturnMetrics, pseudoRowsForBenchmark,
  trailingPeriodDefinitions,
  type HeatmapRange, type ReturnMetric, type RollingWindow,
} from './analytics.js';

function numberOrNull(value: string | null): number | null {
  if (value === null) return null;
  const result = Number(value);
  if (!Number.isFinite(result)) throw new Error('Database returned non-finite numeric data');
  return result;
}

export async function liveTicker(pool: pg.Pool) {
  return calculateDailyTicker(await readLiveTicker(pool));
}

export async function navHistory(
  pool: pg.Pool, sdId: string, fromDate: string, toDate: string,
  page: number, limit: number, includePagination: boolean,
) {
  const matches = await resolveScheme(pool, sdId);
  if (matches.length !== 1) return matches.length === 0 ? null : 'ambiguous' as const;
  const match = matches[0]!;
  const result = await readNavHistory(
    pool, match.source, match.schemeCode, fromDate, toDate, limit, (page - 1) * limit
  );
  return {
    success: true,
    data: result.rows.map((row) => ({ date: row.date, nav: numberOrNull(row.nav) })),
    ...(includePagination ? { pagination: {
      page, limit, total: result.total, pages: Math.ceil(result.total / limit)
    } } : {})
  };
}

export async function benchmarkPerformance(pool: pg.Pool, toDate: string) {
  const history = await readAllBenchmarkHistory(pool, toDate);
  return {
    success: true,
    benchmarks: benchmarkDefinitions.map((benchmark) => {
      const observations = history.filter((row) => row.benchmarkId === benchmark.id)
        .map(({ date, value }) => ({ date, value }));
      const calculated = observations.length
        ? calculateUnifiedPerformance(
          pseudoRowsForBenchmark(benchmark.id, benchmark.label, observations), toDate,
        ).funds[0]
        : null;
      return {
        ...benchmark,
        metrics: {
          r1d: calculated?.returns.r1d.value ?? null,
          r1w: calculated?.returns.r1w.value ?? null,
          r1m: calculated?.returns.r1m.value ?? null,
          r3m: calculated?.returns.r3m.value ?? null,
          // The imported official snapshots begin in September 2025, so they do not
          // establish true index inception and must not be labelled since-inception.
          si: null,
          latestNav: calculated?.latestNav === null || calculated?.latestNav === undefined
            ? null : calculated.latestNav.toFixed(2),
          latestDate: calculated?.latestDate ?? null,
        },
      };
    }),
  };
}

export function sifTer() {
  return { success: true, month: new Date().getUTCMonth() + 1, count: 0, funds: [] };
}

export async function heatmap(pool: pg.Pool, window: '3m'|'6m'|'12m'|'all', toDate: string) {
  const [fundRows, allHistory] = await Promise.all([
    readPerformanceNav(pool, toDate), readAllBenchmarkHistory(pool, toDate),
  ]);
  const result = calculateHeatmap(fundRows, window, toDate);
  const benchmarkMap = Object.fromEntries(result.benchmarks.map((benchmark) => {
    const history = allHistory.filter((row) => row.benchmarkId === benchmark.id)
      .map(({ date, value }) => ({ date, value }));
    const calculated = calculateBenchmarkMonthlyReturns(history, result.months, toDate);
    return [benchmark.id, calculated.values];
  }));
  return { ...result, benchmarkMap };
}

export async function performance(
  pool: pg.Pool, toDate: string, from1m?: string, from3m?: string,
) {
  return calculatePerformance(await readPerformanceNav(pool, toDate), toDate, from1m, from3m);
}

export async function unifiedPerformance(pool: pg.Pool, toDate: string) {
  return calculateUnifiedPerformance(await readPerformanceNav(pool, toDate), toDate);
}

export async function monthlyAnalytics(
  pool: pg.Pool, range: HeatmapRange, toDate: string,
) {
  const [fundRows, benchmarks, allHistory] = await Promise.all([
    readPerformanceNav(pool, toDate), readBenchmarks(pool), readAllBenchmarkHistory(pool, toDate),
  ]);
  const result = calculateMonthlyAnalytics(fundRows, range, toDate);
  const benchmarkMap: Record<string, Record<string, number | null>> = {};
  const benchmarkCellMetadata: Record<string, Record<string, ReturnMetric>> = {};
  for (const benchmark of benchmarks) {
    const history = allHistory.filter((row) => row.benchmarkId === benchmark.id)
      .map(({ date, value }) => ({ date, value }));
    if (history.length === 0) {
      benchmarkMap[benchmark.id] = Object.fromEntries(
        result.months.map((month) => [month.label, null]),
      );
      benchmarkCellMetadata[benchmark.id] = Object.fromEntries(
        result.months.map((month) => [month.label, unavailableBenchmarkMetric()]),
      );
      continue;
    }
    const calculated = calculateBenchmarkMonthlyReturns(history, result.months, toDate);
    benchmarkMap[benchmark.id] = calculated.values;
    benchmarkCellMetadata[benchmark.id] = calculated.metadata;
  }
  return { ...result, benchmarkMap, benchmarkCellMetadata, benchmarks };
}

export async function annualAnalytics(pool: pg.Pool, toDate: string) {
  return calculateAnnualAnalytics(await readPerformanceNav(pool, toDate), toDate);
}

export async function rollingAnalytics(
  pool: pg.Pool, referenceFundId: number, window: RollingWindow, toDate: string,
) {
  return calculateRollingAnalytics(
    await readPerformanceNav(pool, toDate, referenceFundId), window, toDate,
  );
}

export async function trendAnalytics(
  pool: pg.Pool, referenceFundId: number, fromDate: string, toDate: string,
) {
  const grouped = groupSeries(await readPerformanceNav(pool, toDate, referenceFundId), toDate);
  const fund = grouped[0];
  if (!fund) return null;
  const observations = fund.observations.filter((row) => row.date >= fromDate);
  const base = observations[0];
  return {
    success: true, requestedFromDate: fromDate, asOfDate: toDate,
    methodology: 'observed-nav-and-cumulative-absolute-return-v1',
    fund: {
      id: fund.displayOrder, fundId: fund.fundId, fullName: fund.fullName,
      slug: fund.slug, source: fund.source, schemeCode: fund.schemeCode,
    },
    baseDate: base?.date ?? null,
    baseNav: base ? Number(base.nav) : null,
    series: base ? observations.map((observation) => ({
      date: observation.date,
      nav: Number(observation.nav),
      cumulativeReturnPct: percentageReturn(base.nav, observation.nav, false).value,
    })) : [],
    status: base ? 'available' : 'insufficient-history',
  };
}

function unavailableBenchmarkMetric(): ReturnMetric {
  return {
    value: null, requestedStartDate: null, startDate: null, startValue: null,
    endDate: null, endValue: null, actualElapsedDays: null,
    calculationMethod: 'benchmark-history-unavailable', status: 'unavailable',
    warnings: ['verified_benchmark_history_unavailable'],
  };
}

export async function benchmarkCatalog(pool: pg.Pool, toDate: string) {
  const [benchmarks, allHistory] = await Promise.all([
    readBenchmarks(pool), readAllBenchmarkHistory(pool, toDate),
  ]);
  const groupedHistory = new Map<string, Array<{ date: string; value: string }>>();
  for (const row of allHistory) {
    const history = groupedHistory.get(row.benchmarkId) ?? [];
    history.push({ date: row.date, value: row.value });
    groupedHistory.set(row.benchmarkId, history);
  }
  const output = [];
  for (const benchmark of benchmarks) {
    const history = groupedHistory.get(benchmark.id) ?? [];
    const calculated = history.length
      ? calculateUnifiedPerformance(
        pseudoRowsForBenchmark(benchmark.id, benchmark.label, history), toDate,
      ).funds[0]
      : null;
    output.push({
      ...benchmark,
      observationCount: history.length,
      firstDate: history[0]?.date ?? null,
      latestDate: history.at(-1)?.date ?? null,
      returns: calculated?.returns ?? Object.fromEntries(
        trailingPeriodDefinitions.map(({ id }) => [id, unavailableBenchmarkMetric()]),
      ),
    });
  }
  return { success: true, asOfDate: toDate, benchmarks: output };
}

export async function benchmarkComparison(
  pool: pg.Pool, referenceFundId: number, benchmarkId: string, toDate: string,
) {
  const [fundRows, benchmarks] = await Promise.all([
    readPerformanceNav(pool, toDate, referenceFundId), readBenchmarks(pool),
  ]);
  const benchmark = benchmarks.find((item) => item.id === benchmarkId);
  if (!benchmark) return 'benchmark-not-found' as const;
  const fund = calculateUnifiedPerformance(fundRows, toDate).funds[0];
  if (!fund) return 'fund-not-found' as const;
  const history = await readBenchmarkHistory(pool, benchmarkId, toDate);
  const benchmarkFund = history.length
    ? calculateUnifiedPerformance(
      pseudoRowsForBenchmark(benchmark.id, benchmark.label, history), toDate,
    ).funds[0]
    : null;
  const benchmarkReturns = benchmarkFund?.returns ?? Object.fromEntries(
    trailingPeriodDefinitions.map(({ id }) => [id, unavailableBenchmarkMetric()]),
  ) as typeof fund.returns;
  return {
    success: true, asOfDate: toDate,
    methodology: 'absolute-excess-and-relative-compounded-v1',
    fund: { id: fund.id, fullName: fund.fullName, scheme: fund.scheme },
    benchmark: {
      ...benchmark, observationCount: history.length,
      firstDate: history[0]?.date ?? null, latestDate: history.at(-1)?.date ?? null,
    },
    periods: Object.fromEntries(trailingPeriodDefinitions.map(({ id }) => [id, {
      ...compareReturnMetrics(fund.returns[id], benchmarkReturns[id]),
      fundMetadata: fund.returns[id], benchmarkMetadata: benchmarkReturns[id],
    }])),
  };
}

export async function fundDetails(pool: pg.Pool, slug: string) {
  const details = await readFundDetails(pool, slug);
  if (!details) return null;
  return {
    success: true,
    data: {
      ...details.fund,
      schemes: details.schemes.map((scheme) => ({
        ...scheme,
        latestNav: numberOrNull(scheme.latestNav),
        dailyChangePct: numberOrNull(scheme.dailyChangePct)
      })),
      quarterlyAAUM: details.aaum.map((row) => ({
        ...row, aaumLakhs: numberOrNull(row.aaumLakhs), aaumCr: numberOrNull(row.aaumCr)
      })),
      sourceTimestamps: details.source,
      managers: details.managers,
      factsheet: null,
      benchmark: benchmarkAvailability,
      expenseRatio: terAvailability
    }
  };
}
