import type { PreferredNavRow } from '../repositories/funds.js';
import {
  absoluteReturn, addMonth, groupSeries, isoDate, lastAtOrBefore,
  monthEnd, monthLabel, monthStart, monthlyBoundaryObservations, normalizeObservations,
  parseDate, percentageReturn,
  selectReferenceObservation, shiftDays, shiftMonths,
  type FundSeries, type NavObservation,
} from './performance.js';

export type TrailingPeriodId =
  'r1d' | 'r1w' | 'r1m' | 'r3m' | 'r6m' | 'r1y' | 'r2y' | 'r3y' | 'r5y' | 'ytd' | 'si';
export type RollingWindow = '1m' | '3m' | '6m' | '1y';
export type HeatmapRange = '12m' | '24m' | '36m' | '60m' | 'all';

export interface ReturnMetric {
  value: number | null;
  requestedStartDate: string | null;
  startDate: string | null;
  startValue: number | null;
  endDate: string | null;
  endValue: number | null;
  actualElapsedDays: number | null;
  calculationMethod: string;
  status: 'complete' | 'partial' | 'ongoing' | 'insufficient-history' | 'invalid' | 'unavailable';
  warnings: string[];
}

export interface MonthlyPeriod {
  label: string;
  fromDate: string;
  toDate: string;
  completed?: boolean;
}

export const trailingPeriodDefinitions: ReadonlyArray<{
  id: TrailingPeriodId; label: string;
}> = [
  { id: 'r1d', label: '1 Day' },
  { id: 'r1w', label: '1 Week' },
  { id: 'r1m', label: '1 Month' },
  { id: 'r3m', label: '3 Months' },
  { id: 'r6m', label: '6 Months' },
  { id: 'r1y', label: '1 Year' },
  { id: 'r2y', label: '2 Years' },
  { id: 'r3y', label: '3 Years' },
  { id: 'r5y', label: '5 Years' },
  { id: 'ytd', label: 'Year to Date' },
  { id: 'si', label: 'Since Inception' },
];

function elapsedDays(start: string, end: string): number {
  return Math.round((parseDate(end).getTime() - parseDate(start).getTime()) / 86400000);
}

function numeric(value: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) throw new Error('Invalid normalized NAV');
  return parsed;
}

function unavailableMetric(
  method: string, requestedStartDate: string | null,
  status: ReturnMetric['status'] = 'insufficient-history', warnings: string[] = [],
): ReturnMetric {
  return {
    value: null, requestedStartDate, startDate: null, startValue: null,
    endDate: null, endValue: null, actualElapsedDays: null,
    calculationMethod: method, status, warnings,
  };
}

function metric(
  start: NavObservation | undefined, end: NavObservation | undefined,
  requestedStartDate: string | null, method: string,
  status: ReturnMetric['status'] = 'complete', warnings: string[] = [],
): ReturnMetric {
  if (!start || !end || start.date >= end.date) {
    return unavailableMetric(method, requestedStartDate, 'insufficient-history', warnings);
  }
  const value = absoluteReturn(start, end, false);
  if (value === null) {
    return unavailableMetric(method, requestedStartDate, 'invalid', [
      ...warnings, 'return_calculation_failed',
    ]);
  }
  return {
    value,
    requestedStartDate,
    startDate: start.date,
    startValue: numeric(start.nav),
    endDate: end.date,
    endValue: numeric(end.nav),
    actualElapsedDays: elapsedDays(start.date, end.date),
    calculationMethod: method,
    status,
    warnings,
  };
}

function targetForPeriod(period: TrailingPeriodId, toDate: string): string | null {
  if (period === 'r1w') return shiftDays(toDate, 7);
  if (period === 'r1m') return shiftDays(toDate, 30);
  if (period === 'r3m') return shiftDays(toDate, 90);
  if (period === 'r6m') return shiftMonths(toDate, 6);
  if (period === 'r1y') return shiftMonths(toDate, 12);
  if (period === 'r2y') return shiftMonths(toDate, 24);
  if (period === 'r3y') return shiftMonths(toDate, 36);
  if (period === 'r5y') return shiftMonths(toDate, 60);
  if (period === 'ytd') return `${toDate.slice(0, 4)}-01-01`;
  return null;
}

function calculateTrailingMetric(
  observations: NavObservation[], period: TrailingPeriodId, toDate: string,
): ReturnMetric {
  const end = lastAtOrBefore(observations, toDate);
  if (!end) return unavailableMetric('no-valid-ending-observation', null, 'unavailable');
  if (period === 'r1d') {
    const endIndex = observations.findIndex((row) => row === end);
    return metric(observations[endIndex - 1], end, null, 'previous-available-observation');
  }
  if (period === 'si') {
    return metric(observations[0], end, observations[0]?.date ?? null, 'inception-observation');
  }
  const target = targetForPeriod(period, toDate)!;
  if (!observations[0] || observations[0].date > target) {
    return unavailableMetric(
      period === 'ytd' ? 'year-start:on-or-before' : 'target:first-on-or-after',
      target, 'insufficient-history', ['history_begins_after_requested_start'],
    );
  }
  const start = period === 'ytd'
    ? lastAtOrBefore(observations, target)
    : selectReferenceObservation(observations, target, 'on-or-after');
  return metric(
    start, end, target,
    period === 'ytd' ? 'calendar-year-start:on-or-before' :
      `${period === 'r1m' || period === 'r3m' || period === 'r1w' ? 'fixed-days' : 'calendar-months'}:first-on-or-after`,
  );
}

function seriesWarnings(series: FundSeries, datasetLatestDate: string): string[] {
  const warnings = new Set(series.dataQualityWarnings);
  const latest = series.observations.at(-1);
  if (latest && latest.date < datasetLatestDate) warnings.add('latest_before_dataset_max');
  return [...warnings].sort();
}

export function calculateUnifiedPerformance(rows: PreferredNavRow[], toDate: string) {
  const series = groupSeries(rows, toDate);
  const datasetLatestDate = series.map((fund) => fund.observations.at(-1)?.date ?? '')
    .sort().at(-1) ?? '';
  const funds = series.map((fund) => {
    const latest = fund.observations.at(-1);
    const inception = fund.observations[0];
    const warnings = seriesWarnings(fund, datasetLatestDate);
    const invalid = warnings.includes('conflicting_duplicate_observation');
    const returns = Object.fromEntries(trailingPeriodDefinitions.map(({ id }) => [
      id,
      invalid ? unavailableMetric('invalid-series', targetForPeriod(id, toDate), 'invalid', warnings)
        : calculateTrailingMetric(fund.observations, id, toDate),
    ])) as Record<TrailingPeriodId, ReturnMetric>;
    return {
      id: fund.displayOrder,
      fundId: fund.fundId,
      fullName: fund.fullName,
      slug: fund.slug,
      scheme: { source: fund.source, schemeCode: fund.schemeCode, plan: 'Regular', option: 'Growth' },
      latestNav: latest ? numeric(latest.nav) : null,
      latestDate: latest?.date ?? null,
      inceptionDate: inception?.date ?? null,
      dataQualityStatus: invalid ? 'invalid' : warnings.length ? 'warning' : 'ok',
      dataQualityWarnings: warnings,
      returns,
    };
  });
  return {
    success: true,
    asOfDate: toDate,
    methodology: 'sif-unified-returns-v1',
    referenceSelection: {
      daily: 'immediately preceding available observation',
      oneWeek: '7 calendar days, first NAV on or after target',
      oneMonth: '30 calendar days, first NAV on or after target',
      threeMonths: '90 calendar days, first NAV on or after target',
      sixMonthsAndYears: 'calendar subtraction, first NAV on or after target',
      ytd: 'last NAV on or before 1 January',
    },
    periods: trailingPeriodDefinitions,
    funds,
  };
}

function rangeMonths(range: HeatmapRange): number | null {
  return range === 'all' ? null : Number(range.slice(0, -1));
}

export function calculateMonthlyAnalytics(
  rows: PreferredNavRow[], range: HeatmapRange, toDate: string,
) {
  const series = groupSeries(rows, toDate);
  const count = rangeMonths(range);
  const earliest = series.map((fund) => fund.observations[0]?.date).filter(Boolean).sort()[0];
  const firstMonth = count === null
    ? monthStart(earliest ?? toDate)
    : shiftMonths(monthStart(toDate), count - 1);
  const finalMonth = monthStart(toDate);
  const months: Array<{
    key: string; label: string; fromDate: string; toDate: string;
    completed: boolean; status: 'complete' | 'partial';
  }> = [];
  for (let current = firstMonth; current <= finalMonth; current = addMonth(current)) {
    const boundary = monthEnd(current);
    const completed = boundary <= toDate;
    months.push({
      key: current.slice(0, 7), label: monthLabel(current), fromDate: current,
      toDate: completed ? boundary : toDate, completed,
      status: completed ? 'complete' : 'partial',
    });
  }
  const monthlyMap: Record<string, Record<string, number | null>> = {};
  const cellMetadata: Record<string, Record<string, ReturnMetric>> = {};
  const funds = series.map((fund) => ({
    id: fund.displayOrder, fundId: fund.fundId, fullName: fund.fullName,
    slug: fund.slug, source: fund.source, schemeCode: fund.schemeCode,
    plan: 'Regular', option: 'Growth',
  }));
  for (const fund of series) {
    const key = String(fund.displayOrder);
    monthlyMap[key] = {};
    cellMetadata[key] = {};
    for (const period of months) {
      const boundary = monthlyBoundaryObservations(
        fund.observations, period.fromDate, period.toDate,
      );
      const calculated = metric(
        boundary.start, boundary.end, period.fromDate,
        'calendar-month-boundaries:closest-observation',
        period.completed && !boundary.usesInception ? 'complete' : 'partial',
        boundary.usesInception ? ['month_uses_inception'] : [],
      );
      monthlyMap[key]![period.label] = calculated.value;
      cellMetadata[key]![period.label] = calculated;
    }
  }
  return {
    success: true, asOfDate: toDate, range,
    methodology: 'calendar-month-boundaries-closest-observation-v1',
    funds, months, monthlyMap, cellMetadata,
  };
}

export function calculateBenchmarkMonthlyReturns(
  observations: Array<{ date: string; value: string }>,
  periods: MonthlyPeriod[], asOfDate: string,
) {
  const normalized = normalizeObservations(
    observations.map(({ date, value }) => ({ date, nav: value })), asOfDate,
  );
  const values: Record<string, number | null> = {};
  const metadata: Record<string, ReturnMetric> = {};
  for (const period of periods) {
    const endTarget = period.toDate < asOfDate ? period.toDate : asOfDate;
    const start = lastAtOrBefore(normalized.observations, shiftDays(period.fromDate, 1));
    const end = lastAtOrBefore(normalized.observations, endTarget);
    const warnings = [...normalized.warnings];
    if (end && end.date < endTarget) warnings.push('latest_close_before_requested_end');
    const calculated = metric(
      start, end, period.fromDate,
      'calendar-month-close-to-close:last-on-or-before',
      period.completed === false || period.toDate > asOfDate ? 'partial' : 'complete',
      [...new Set(warnings)].sort(),
    );
    values[period.label] = calculated.value;
    metadata[period.label] = calculated;
  }
  return { values, metadata };
}

function yearRange(startYear: number, endYear: number): number[] {
  return Array.from({ length: endYear - startYear + 1 }, (_, index) => startYear + index);
}

function financialYearLabel(startYear: number): string {
  return `FY ${startYear}-${String(startYear + 1).slice(-2)}`;
}

export function calculateAnnualAnalytics(rows: PreferredNavRow[], toDate: string) {
  const series = groupSeries(rows, toDate);
  const earliestDate = series.map((fund) => fund.observations[0]?.date)
    .filter(Boolean).sort()[0] ?? toDate;
  const firstYear = Number(earliestDate.slice(0, 4));
  const currentYear = Number(toDate.slice(0, 4));
  const calendarPeriods = yearRange(firstYear, currentYear).map((year) => ({
    key: String(year), label: String(year), fromDate: `${year}-01-01`,
    toDate: year === currentYear ? toDate : `${year}-12-31`,
    completed: year < currentYear,
  }));
  const currentMonth = Number(toDate.slice(5, 7));
  const earliestMonth = Number(earliestDate.slice(5, 7));
  const firstFiscalStart = earliestMonth >= 4 ? firstYear : firstYear - 1;
  const currentFiscalStart = currentMonth >= 4 ? currentYear : currentYear - 1;
  const financialPeriods = yearRange(firstFiscalStart, currentFiscalStart).map((year) => {
    const end = `${year + 1}-03-31`;
    return {
      key: financialYearLabel(year), label: financialYearLabel(year),
      fromDate: `${year}-04-01`, toDate: end <= toDate ? end : toDate,
      completed: end <= toDate,
    };
  });
  const calendarYearMap: Record<string, Record<string, number | null>> = {};
  const financialYearMap: Record<string, Record<string, number | null>> = {};
  const ytdMap: Record<string, ReturnMetric> = {};
  const financialYtdMap: Record<string, ReturnMetric> = {};
  const metadata: Record<string, { calendar: Record<string, ReturnMetric>; financial: Record<string, ReturnMetric> }> = {};
  for (const fund of series) {
    const id = String(fund.displayOrder);
    calendarYearMap[id] = {};
    financialYearMap[id] = {};
    metadata[id] = { calendar: {}, financial: {} };
    for (const period of calendarPeriods) {
      const calculated = metric(
        lastAtOrBefore(fund.observations, period.fromDate),
        lastAtOrBefore(fund.observations, period.toDate),
        period.fromDate, 'calendar-year-boundaries:on-or-before',
        period.completed ? 'complete' : 'ongoing',
      );
      metadata[id]!.calendar[period.key] = calculated;
      calendarYearMap[id]![period.key] = period.completed ? calculated.value : null;
      if (!period.completed) ytdMap[id] = calculated;
    }
    for (const period of financialPeriods) {
      const calculated = metric(
        lastAtOrBefore(fund.observations, period.fromDate),
        lastAtOrBefore(fund.observations, period.toDate),
        period.fromDate, 'financial-year-boundaries:on-or-before',
        period.completed ? 'complete' : 'ongoing',
      );
      metadata[id]!.financial[period.key] = calculated;
      financialYearMap[id]![period.key] = period.completed ? calculated.value : null;
      if (!period.completed) financialYtdMap[id] = calculated;
    }
  }
  return {
    success: true, asOfDate: toDate,
    methodology: 'calendar-and-indian-financial-year-boundaries-v1',
    funds: series.map((fund) => ({ id: fund.displayOrder, fullName: fund.fullName, slug: fund.slug })),
    calendarPeriods, financialPeriods,
    calendarYearMap, financialYearMap, ytdMap, financialYtdMap, metadata,
  };
}

function rollingTarget(date: string, window: RollingWindow): string {
  if (window === '1m') return shiftDays(date, 30);
  if (window === '3m') return shiftDays(date, 90);
  return shiftMonths(date, window === '6m' ? 6 : 12);
}

function roundedHundredthsRatio(numerator: number, denominator: number): number {
  const negative = numerator < 0;
  const absolute = Math.abs(numerator);
  let quotient = Math.floor(absolute / denominator);
  if ((absolute % denominator) * 2 >= denominator) quotient += 1;
  return (negative ? -quotient : quotient) / 100;
}

function rollingSummary(values: number[]) {
  if (values.length === 0) {
    return { average: null, minimum: null, maximum: null, median: null, validObservations: 0 };
  }
  const hundredths = values.map((value) => Math.round(value * 100)).sort((a, b) => a - b);
  const total = hundredths.reduce((sum, value) => sum + value, 0);
  const middle = Math.floor(hundredths.length / 2);
  const median = hundredths.length % 2
    ? hundredths[middle]! / 100
    : roundedHundredthsRatio(hundredths[middle - 1]! + hundredths[middle]!, 2);
  return {
    average: roundedHundredthsRatio(total, hundredths.length),
    minimum: hundredths[0]! / 100,
    maximum: hundredths.at(-1)! / 100,
    median,
    validObservations: hundredths.length,
  };
}

export function calculateRollingAnalytics(
  rows: PreferredNavRow[], window: RollingWindow, toDate: string,
) {
  const fund = groupSeries(rows, toDate)[0];
  if (!fund) return null;
  const points: Array<ReturnMetric & { observationDate: string }> = [];
  for (const end of fund.observations) {
    const target = rollingTarget(end.date, window);
    if (!fund.observations[0] || fund.observations[0].date > target) continue;
    const start = selectReferenceObservation(fund.observations, target, 'on-or-after');
    if (!start || start.date >= end.date) continue;
    points.push({
      ...metric(start, end, target, 'rolling:first-on-or-after'),
      observationDate: end.date,
    });
  }
  return {
    success: true, asOfDate: toDate, window,
    methodology: 'rolling-calendar-target-first-on-or-after-v1',
    fund: {
      id: fund.displayOrder, fundId: fund.fundId, fullName: fund.fullName,
      slug: fund.slug, source: fund.source, schemeCode: fund.schemeCode,
    },
    summary: rollingSummary(points.flatMap((point) => point.value === null ? [] : [point.value])),
    series: points,
  };
}

function decimalDifference(first: number, second: number): number {
  return (Math.round(first * 100) - Math.round(second * 100)) / 100;
}

export function compareReturnMetrics(fund: ReturnMetric, benchmark: ReturnMetric) {
  if (fund.value === null || benchmark.value === null) {
    return {
      fundReturn: fund.value, benchmarkReturn: benchmark.value,
      excessReturnPctPoints: null, relativeReturnPct: null,
      status: 'unavailable' as const,
    };
  }
  const relative = percentageReturn(
    String(100 + benchmark.value), String(100 + fund.value), false,
  ).value;
  return {
    fundReturn: fund.value,
    benchmarkReturn: benchmark.value,
    excessReturnPctPoints: decimalDifference(fund.value, benchmark.value),
    relativeReturnPct: relative,
    status: relative === null ? 'invalid' as const : 'complete' as const,
  };
}

export function pseudoRowsForBenchmark(
  id: string, label: string, observations: Array<{ date: string; value: string }>,
): PreferredNavRow[] {
  return observations.map((observation) => ({
    displayOrder: 1, fundId: 0, fullName: label, slug: id, source: 'BENCHMARK',
    schemeCode: 0, date: observation.date, nav: observation.value,
  }));
}
