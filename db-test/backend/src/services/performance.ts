import type { PreferredNavRow } from '../repositories/funds.js';
import { benchmarkDefinitions, emptyBenchmarkMap } from './benchmark.js';

export interface NavObservation { date: string; nav: string }

export interface FundSeries {
  displayOrder: number;
  fundId: number;
  fullName: string;
  slug: string;
  source: string;
  schemeCode: number;
  observations: NavObservation[];
  dataQualityWarnings: string[];
}

export type ReferenceSelection = 'on-or-before' | 'on-or-after' | 'strictly-after';

export interface PerformanceCalculationPolicy {
  id: string;
  oneMonthSelection: ReferenceSelection;
  threeMonthSelection: ReferenceSelection;
  zeroReturnAsNull: boolean;
  fallbackToInception: boolean;
}

export const SIF360_OBSERVED_POLICY: PerformanceCalculationPolicy = {
  id: 'sif360-observed-30d-90d-v1',
  oneMonthSelection: 'on-or-after',
  threeMonthSelection: 'on-or-after',
  zeroReturnAsNull: true,
  fallbackToInception: true,
};

export interface NormalizedPerformanceResult {
  schemeId: string;
  latestNav: number | null;
  latestDate: string | null;
  inceptionDate: string | null;
  changePct: number | null;
  return1m: number | null;
  return3m: number | null;
  referenceNav1m: number | null;
  referenceDate1m: string | null;
  referenceNav3m: number | null;
  referenceDate3m: string | null;
  calculationStatus: 'complete' | 'partial' | 'invalid' | 'unavailable';
  calculationMethod: string;
  dataQualityWarnings: string[];
}

interface DecimalParts { coefficient: bigint; scale: number }

function parseDecimal(value: string): DecimalParts | null {
  const trimmed = value.trim();
  if (!/^\+?\d+(?:\.\d+)?$/.test(trimmed)) return null;
  const unsigned = trimmed.startsWith('+') ? trimmed.slice(1) : trimmed;
  const [whole = '0', fraction = ''] = unsigned.split('.');
  return { coefficient: BigInt(`${whole}${fraction}`), scale: fraction.length };
}

function power10(value: number): bigint { return 10n ** BigInt(value); }

function alignDecimals(first: DecimalParts, second: DecimalParts) {
  const scale = Math.max(first.scale, second.scale);
  return {
    first: first.coefficient * power10(scale - first.scale),
    second: second.coefficient * power10(scale - second.scale),
  };
}

function compareDecimalStrings(first: string, second: string): number {
  const left = parseDecimal(first);
  const right = parseDecimal(second);
  if (!left || !right) return first.localeCompare(second);
  const aligned = alignDecimals(left, right);
  return aligned.first < aligned.second ? -1 : aligned.first > aligned.second ? 1 : 0;
}

function decimalRatio(numerator: bigint, denominator: bigint, places = 12): string {
  const negative = numerator < 0n;
  const absolute = negative ? -numerator : numerator;
  const scaled = absolute * power10(places);
  const quotient = scaled / denominator;
  const digits = quotient.toString().padStart(places + 1, '0');
  const whole = digits.slice(0, -places) || '0';
  const fraction = digits.slice(-places).replace(/0+$/, '');
  return `${negative ? '-' : ''}${whole}${fraction ? `.${fraction}` : ''}`;
}

export function percentageReturn(
  startNav: string, endNav: string, zeroReturnAsNull = false,
): { value: number | null; unrounded: string | null } {
  const start = parseDecimal(startNav);
  const end = parseDecimal(endNav);
  if (!start || !end || start.coefficient <= 0n || end.coefficient <= 0n) {
    return { value: null, unrounded: null };
  }
  const aligned = alignDecimals(start, end);
  const difference = aligned.second - aligned.first;
  const percentNumerator = difference * 100n;
  const unrounded = decimalRatio(percentNumerator, aligned.first);
  const hundredthsNumerator = difference * 10000n;
  const negative = hundredthsNumerator < 0n;
  const absolute = negative ? -hundredthsNumerator : hundredthsNumerator;
  let hundredths = absolute / aligned.first;
  if ((absolute % aligned.first) * 2n >= aligned.first) hundredths += 1n;
  if (negative) hundredths = -hundredths;
  if (zeroReturnAsNull && hundredths === 0n) return { value: null, unrounded };
  return { value: Number(hundredths) / 100, unrounded };
}

function numeric(value: string): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) throw new Error('Invalid positive NAV');
  return parsed;
}

export function validIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00.000Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

export function parseDate(value: string): Date { return new Date(`${value}T00:00:00.000Z`); }
export function isoDate(value: Date): string { return value.toISOString().slice(0, 10); }

export function shiftDays(value: string, days: number): string {
  const date = parseDate(value);
  date.setUTCDate(date.getUTCDate() - days);
  return isoDate(date);
}

export function shiftMonths(value: string, months: number): string {
  const date = parseDate(value);
  const day = date.getUTCDate();
  date.setUTCDate(1);
  date.setUTCMonth(date.getUTCMonth() - months);
  const end = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
  date.setUTCDate(Math.min(day, end));
  return isoDate(date);
}

export function monthStart(value: string): string { return `${value.slice(0, 7)}-01`; }
export function monthEnd(value: string): string {
  const date = parseDate(monthStart(value));
  date.setUTCMonth(date.getUTCMonth() + 1);
  date.setUTCDate(0);
  return isoDate(date);
}

export function monthLabel(value: string): string {
  const date = parseDate(value);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sept', 'Oct', 'Nov', 'Dec'];
  return `${months[date.getUTCMonth()]} '${String(date.getUTCFullYear()).slice(2)}`;
}

export function addMonth(value: string): string {
  const date = parseDate(monthStart(value));
  date.setUTCMonth(date.getUTCMonth() + 1);
  return isoDate(date);
}

export function normalizeObservations(
  observations: NavObservation[], toDate: string,
): { observations: NavObservation[]; warnings: string[]; conflicting: boolean } {
  const byDate = new Map<string, string[]>();
  const warnings = new Set<string>();
  for (const observation of observations) {
    const decimal = parseDecimal(observation.nav);
    if (!validIsoDate(observation.date) || observation.date > toDate
        || !decimal || decimal.coefficient <= 0n) {
      warnings.add('invalid_observation_excluded');
      continue;
    }
    const values = byDate.get(observation.date) ?? [];
    values.push(observation.nav);
    byDate.set(observation.date, values);
  }
  let conflicting = false;
  const normalized = [...byDate.entries()].sort(([left], [right]) => left.localeCompare(right))
    .map(([date, values]) => {
      values.sort(compareDecimalStrings);
      const unique = values.filter((value, index) => index === 0
        || compareDecimalStrings(value, values[index - 1]!) !== 0);
      if (values.length > 1 && unique.length === 1) warnings.add('duplicate_observation_deduplicated');
      if (unique.length > 1) {
        conflicting = true;
        warnings.add('conflicting_duplicate_observation');
      }
      return { date, nav: unique[0]! };
    });
  return { observations: normalized, warnings: [...warnings].sort(), conflicting };
}

export function groupSeries(rows: PreferredNavRow[], toDate: string): FundSeries[] {
  const grouped = new Map<number, Omit<FundSeries, 'observations' | 'dataQualityWarnings'> & {
    rawObservations: NavObservation[];
  }>();
  for (const row of rows) {
    let series = grouped.get(row.displayOrder);
    if (!series) {
      series = {
        displayOrder: row.displayOrder, fundId: row.fundId, fullName: row.fullName,
        slug: row.slug, source: row.source, schemeCode: row.schemeCode,
        rawObservations: [],
      };
      grouped.set(row.displayOrder, series);
    }
    series.rawObservations.push({ date: row.date, nav: row.nav });
  }
  return [...grouped.values()].map((series) => {
    const normalized = normalizeObservations(series.rawObservations, toDate);
    return {
      ...series, observations: normalized.observations,
      dataQualityWarnings: normalized.warnings,
    };
  }).sort((a, b) => a.displayOrder - b.displayOrder);
}

export function selectReferenceObservation(
  rows: NavObservation[], date: string, selection: ReferenceSelection,
): NavObservation | undefined {
  if (selection === 'on-or-before') {
    for (let index = rows.length - 1; index >= 0; index -= 1) {
      const row = rows[index];
      if (row && row.date <= date) return row;
    }
    return undefined;
  }
  return rows.find((row) => selection === 'on-or-after' ? row.date >= date : row.date > date);
}

export function lastAtOrBefore(rows: NavObservation[], date: string): NavObservation | undefined {
  return selectReferenceObservation(rows, date, 'on-or-before');
}

export function closestObservation(
  rows: NavObservation[], targetDate: string,
): NavObservation | undefined {
  const target = parseDate(targetDate).getTime();
  return rows.reduce<NavObservation | undefined>((closest, candidate) => {
    if (!closest) return candidate;
    const candidateDistance = Math.abs(parseDate(candidate.date).getTime() - target);
    const closestDistance = Math.abs(parseDate(closest.date).getTime() - target);
    return candidateDistance < closestDistance
      || (candidateDistance === closestDistance && candidate.date < closest.date)
      ? candidate : closest;
  }, undefined);
}

export function monthlyBoundaryObservations(
  rows: NavObservation[], fromDate: string, toDate: string,
): { start: NavObservation | undefined; end: NavObservation | undefined; usesInception: boolean } {
  const start = closestObservation(rows, fromDate);
  return {
    start,
    end: closestObservation(rows, toDate),
    usesInception: start !== undefined && start === rows[0] && start.date > fromDate,
  };
}

export function displayDate(value: string): string {
  const date = parseDate(value);
  return `${String(date.getUTCDate()).padStart(2, '0')} ${
    ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][date.getUTCMonth()]
  } ${date.getUTCFullYear()}`;
}

export function absoluteReturn(
  start: NavObservation, end: NavObservation, zeroReturnAsNull = false,
): number | null {
  if (start.date === end.date) return null;
  return percentageReturn(start.nav, end.nav, zeroReturnAsNull).value;
}

export function calculateSchemePerformance(
  input: {
    source: string;
    schemeCode: number;
    observations: NavObservation[];
    dataQualityWarnings?: string[];
  },
  toDate: string,
  target1m = shiftDays(toDate, 30),
  target3m = shiftDays(toDate, 90),
  policy: PerformanceCalculationPolicy = SIF360_OBSERVED_POLICY,
): NormalizedPerformanceResult {
  const normalized = normalizeObservations(input.observations, toDate);
  const warnings = new Set([...(input.dataQualityWarnings ?? []), ...normalized.warnings]);
  const observations = normalized.observations;
  const schemeId = `${input.source}:${input.schemeCode}`;
  const empty = (status: 'invalid' | 'unavailable'): NormalizedPerformanceResult => ({
    schemeId, latestNav: null, latestDate: null, inceptionDate: null,
    changePct: null, return1m: null, return3m: null,
    referenceNav1m: null, referenceDate1m: null,
    referenceNav3m: null, referenceDate3m: null,
    calculationStatus: status, calculationMethod: policy.id,
    dataQualityWarnings: [...warnings].sort(),
  });
  if (!observations.length) {
    warnings.add('no_valid_observations');
    return empty('unavailable');
  }
  if (normalized.conflicting || warnings.has('conflicting_duplicate_observation')) {
    return empty('invalid');
  }

  const inception = observations[0]!;
  const latest = observations.at(-1)!;
  let reference1m = selectReferenceObservation(
    observations, target1m, policy.oneMonthSelection,
  );
  let reference3m = selectReferenceObservation(
    observations, target3m, policy.threeMonthSelection,
  );
  let partial = warnings.has('invalid_observation_excluded');
  if (!reference1m && policy.fallbackToInception && target1m < inception.date) {
    reference1m = inception;
  }
  if (!reference3m && policy.fallbackToInception && target3m < inception.date) {
    reference3m = inception;
  }
  if (reference1m === inception && target1m < inception.date) {
    partial = true;
    warnings.add('one_month_uses_inception');
  }
  if (reference3m === inception && target3m < inception.date) {
    partial = true;
    warnings.add('three_month_uses_inception');
  }
  if (!reference1m) {
    partial = true;
    warnings.add('one_month_reference_unavailable');
  }
  if (!reference3m) {
    partial = true;
    warnings.add('three_month_reference_unavailable');
  }

  const changePct = absoluteReturn(inception, latest, policy.zeroReturnAsNull);
  const return1m = reference1m
    ? absoluteReturn(reference1m, latest, policy.zeroReturnAsNull) : null;
  const return3m = reference3m
    ? absoluteReturn(reference3m, latest, policy.zeroReturnAsNull) : null;
  if (policy.zeroReturnAsNull) {
    for (const [metric, start, result] of [
      ['change_pct', inception, changePct],
      ['one_month', reference1m, return1m],
      ['three_month', reference3m, return3m],
    ] as const) {
      if (start && start.date !== latest.date && result === null
          && percentageReturn(start.nav, latest.nav, false).value === 0) {
        warnings.add(`${metric}_zero_normalized_to_null`);
      }
      if (start?.date === latest.date) warnings.add(`${metric}_has_no_elapsed_period`);
    }
  }
  return {
    schemeId,
    latestNav: numeric(latest.nav), latestDate: latest.date,
    inceptionDate: inception.date,
    changePct, return1m, return3m,
    referenceNav1m: reference1m ? numeric(reference1m.nav) : null,
    referenceDate1m: reference1m?.date ?? null,
    referenceNav3m: reference3m ? numeric(reference3m.nav) : null,
    referenceDate3m: reference3m?.date ?? null,
    calculationStatus: partial ? 'partial' : 'complete',
    calculationMethod: policy.id,
    dataQualityWarnings: [...warnings].sort(),
  };
}

export function calculateHeatmap(rows: PreferredNavRow[], window: '3m' | '6m' | '12m' | 'all', toDate: string) {
  const series = groupSeries(rows, toDate);
  const monthsBack = window === 'all' ? 23 : Number(window.slice(0, -1));
  const requestedStart = monthStart(shiftMonths(toDate, monthsBack));
  const monthlyMap: Record<string, Record<string, number | null>> = {};
  const months: Array<{ label: string; fromDate: string; toDate: string }> = [];
  for (let current = requestedStart; current <= monthStart(toDate); current = addMonth(current)) {
    months.push({ label: monthLabel(current), fromDate: current, toDate: monthEnd(current) });
  }

  for (const fund of series) {
    const key = String(fund.displayOrder);
    monthlyMap[key] = Object.fromEntries(months.map((month) => [month.label, null]));
    const first = fund.observations[0];
    if (!first) continue;
    let current = requestedStart > monthStart(first.date)
      ? requestedStart : monthStart(first.date);
    const finalMonth = monthStart(toDate);
    while (current <= finalMonth) {
      const endBoundary = monthEnd(current) < toDate ? monthEnd(current) : toDate;
      const { start: starting, end: ending } = monthlyBoundaryObservations(
        fund.observations, current, endBoundary,
      );
      const label = monthLabel(current);
      if (ending && ending.date >= current) {
        const value = starting ? absoluteReturn(starting, ending) : null;
        if (value !== null) monthlyMap[key]![label] = value;
      }
      current = addMonth(current);
    }
  }
  return {
    success: true, monthlyMap, months,
    benchmarkMap: emptyBenchmarkMap(), benchmarks: benchmarkDefinitions,
  };
}

export function calculatePerformance(
  rows: PreferredNavRow[], toDate: string, from1m?: string, from3m?: string,
  policy: PerformanceCalculationPolicy = SIF360_OBSERVED_POLICY,
) {
  const series = groupSeries(rows, toDate);
  const target1m = from1m ?? shiftDays(toDate, 30);
  const target3m = from3m ?? shiftDays(toDate, 90);
  const performanceMap: Record<string, Record<string, string | number | null>> = {};
  let isBadData = false;
  for (const fund of series) {
    const result = calculateSchemePerformance(fund, toDate, target1m, target3m, policy);
    if (result.calculationStatus === 'invalid' || result.calculationStatus === 'unavailable'
        || result.latestDate === null || result.inceptionDate === null) {
      isBadData = true;
      continue;
    }
    performanceMap[String(fund.displayOrder)] = {
      changePct: result.changePct,
      return1m: result.return1m,
      return3m: result.return3m,
      latestNav: result.latestNav,
      latestDate: displayDate(result.latestDate),
      inceptionDate: displayDate(result.inceptionDate),
    };
  }
  return { success: true, isBadData, performanceMap };
}
