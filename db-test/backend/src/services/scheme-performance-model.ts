import { createHash } from 'node:crypto';
import { Decimal } from 'decimal.js';
import { isIsoDate } from '../utils/http.js';
import { shiftMonths } from './performance.js';

export const FinancialDecimal = Decimal.clone({ precision: 50, rounding: Decimal.ROUND_HALF_UP });
export const PERFORMANCE_VERSION = 'calendar-strict-sample252-v1';
export const performancePeriods = ['1M', '3M', '6M', '1Y', 'SI'] as const;
export type PerformancePeriod = typeof performancePeriods[number];
export interface SchemeInfo {
  schemeId: string; source: string; schemeCode: string; externalSchemeId: string | null;
  schemeName: string; plan: string | null; option: string | null; amc: string | null;
}
export interface NavInput { date: string; nav: unknown }
export interface SchemeSeries { scheme: SchemeInfo; observations: NavInput[] }
export interface ValidNav { date: string; nav: Decimal }
export interface PreparedScheme {
  scheme: SchemeInfo; observations: ValidNav[]; asOfDate: string | null;
  current: ValidNav | null; warnings: string[]; sourceFingerprint: string;
}
export interface AbsoluteMetric {
  targetDate: string | null; startingNav: Decimal | null; startingNavDate: string | null;
  absoluteReturn: Decimal | null; status: 'complete' | 'insufficient-history';
}
export interface VolatilityMetric {
  annualizedVolatility: Decimal | null; observationCount: number;
  firstReturnDate: string | null; previousNavDate: string | null;
}
export interface SchemePeriodMetric extends AbsoluteMetric, VolatilityMetric {}
export interface SchemePerformance {
  scheme: SchemeInfo; asOfDate: string | null; currentNav: Decimal | null;
  currentNavDate: string | null; inceptionDate: string | null;
  periods: Record<PerformancePeriod, SchemePeriodMetric>; warnings: string[];
  sourceFingerprint: string; calculationVersion: string;
}

export function positiveNav(value: unknown): Decimal | null {
  if (typeof value !== 'string' && typeof value !== 'number' && !Decimal.isDecimal(value)) return null;
  if (typeof value === 'string' && !value.trim()) return null;
  try {
    const decimal = new FinancialDecimal(value as Decimal.Value);
    return decimal.isFinite() && decimal.gt(0) ? decimal : null;
  } catch { return null; }
}

export function normalizeSchemeNav(input: readonly NavInput[], asOfDate?: string) {
  if (asOfDate !== undefined && !isIsoDate(asOfDate)) throw new Error('Invalid as-of date');
  const byDate = new Map<string, Decimal>();
  const warnings = new Set<string>();
  for (const row of input) {
    if (!isIsoDate(row.date)) { warnings.add('invalid_nav_excluded'); continue; }
    if (asOfDate && row.date > asOfDate) continue;
    const nav = positiveNav(row.nav);
    if (!nav) { warnings.add('invalid_nav_excluded'); continue; }
    const previous = byDate.get(row.date);
    if (previous && !previous.eq(nav)) throw new Error(`Conflicting NAV observations on ${row.date}`);
    if (previous) warnings.add('duplicate_nav_deduplicated');
    byDate.set(row.date, nav);
  }
  const observations = [...byDate].sort(([a], [b]) => a.localeCompare(b))
    .map(([date, nav]) => ({ date, nav }));
  return { observations, warnings: [...warnings].sort() };
}

export function prepareScheme(input: SchemeSeries, asOfDate?: string): PreparedScheme {
  const normalized = normalizeSchemeNav(input.observations, asOfDate);
  const current = normalized.observations.at(-1) ?? null;
  return {
    ...normalized, scheme: input.scheme, current, asOfDate: asOfDate ?? current?.date ?? null,
    sourceFingerprint: createHash('sha256').update(JSON.stringify({
      version: PERFORMANCE_VERSION, scheme: input.scheme,
      observations: normalized.observations.map(({ date, nav }) => [date, nav.toString()]),
      warnings: normalized.warnings,
    })).digest('hex'),
  };
}

export function periodTarget(period: PerformancePeriod, asOfDate: string | null): string | null {
  if (!asOfDate || period === 'SI') return null;
  return shiftMonths(asOfDate, { '1M': 1, '3M': 3, '6M': 6, '1Y': 12 }[period]);
}

export function displayDecimal(value: Decimal | null): number | null {
  return value === null ? null : value.toDecimalPlaces(2, Decimal.ROUND_HALF_UP).toNumber();
}

export function serializeSchemePerformance(result: SchemePerformance, period?: PerformancePeriod) {
  return {
    ...result.scheme, asOfDate: result.asOfDate,
    currentNav: result.currentNav?.toNumber() ?? null, currentNavDate: result.currentNavDate,
    inceptionDate: result.inceptionDate, calculationVersion: result.calculationVersion,
    warnings: result.warnings,
    periods: Object.fromEntries(performancePeriods.filter((item) => !period || item === period).map((item) => {
      const metric = result.periods[item];
      return [item, {
        absoluteReturn: displayDecimal(metric.absoluteReturn),
        annualizedVolatility: displayDecimal(metric.annualizedVolatility),
        startingNav: metric.startingNav?.toNumber() ?? null, startingNavDate: metric.startingNavDate,
        targetDate: metric.targetDate, observationCount: metric.observationCount,
        firstReturnDate: metric.firstReturnDate, previousNavDate: metric.previousNavDate,
        status: metric.status,
        volatilityStatus: metric.observationCount >= 2 ? 'complete' : 'insufficient-history',
        precision: {
          absoluteReturn: metric.absoluteReturn?.toString() ?? null,
          annualizedVolatility: metric.annualizedVolatility?.toString() ?? null,
          startingNav: metric.startingNav?.toString() ?? null,
          currentNav: result.currentNav?.toString() ?? null,
        },
      }];
    })),
  };
}
