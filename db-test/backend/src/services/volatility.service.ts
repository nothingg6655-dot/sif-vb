import type { Decimal } from 'decimal.js';
import {
  FinancialDecimal, normalizeSchemeNav, periodTarget, performancePeriods, prepareScheme,
  type NavInput, type PerformancePeriod, type PreparedScheme, type SchemeSeries,
  type ValidNav, type VolatilityMetric,
} from './scheme-performance-model.js';

export interface DailyReturn { date: string; previousNavDate: string; value: Decimal }

function dailyReturnsFromValid(observations: ValidNav[]): DailyReturn[] {
  return observations.slice(1).map((current, index) => ({
    date: current.date, previousNavDate: observations[index]!.date,
    value: current.nav.div(observations[index]!.nav).minus(1),
  }));
}

export function calculateDailyReturns(observations: readonly NavInput[]): DailyReturn[] {
  return dailyReturnsFromValid(normalizeSchemeNav(observations).observations);
}

export function calculateSampleStandardDeviation(values: readonly Decimal.Value[]): Decimal | null {
  if (values.length < 2) return null;
  let mean = new FinancialDecimal(0);
  let sumSquares = new FinancialDecimal(0);
  values.forEach((value, index) => {
    const sample = new FinancialDecimal(value);
    if (!sample.isFinite()) throw new Error('Daily returns must be finite');
    const delta = sample.minus(mean);
    mean = mean.plus(delta.div(index + 1));
    sumSquares = sumSquares.plus(delta.times(sample.minus(mean)));
  });
  return FinancialDecimal.max(0, sumSquares.div(values.length - 1)).sqrt();
}

export function calculateAnnualizedVolatility(values: readonly Decimal.Value[]): Decimal | null {
  return calculateSampleStandardDeviation(values)?.times(new FinancialDecimal(252).sqrt()).times(100) ?? null;
}

export function volatilityForPrepared(input: PreparedScheme): Record<PerformancePeriod, VolatilityMetric> {
  // Compute daily returns before applying period filters so the first return
  // after the boundary retains its immediately preceding valid NAV as the base.
  const daily = dailyReturnsFromValid(input.observations);
  return Object.fromEntries(performancePeriods.map((period) => {
    const target = periodTarget(period, input.asOfDate);
    const window = daily.filter((row) => (target === null || row.date > target)
      && input.asOfDate !== null && row.date <= input.asOfDate);
    return [period, {
      annualizedVolatility: calculateAnnualizedVolatility(window.map((row) => row.value)),
      observationCount: window.length, firstReturnDate: window[0]?.date ?? null,
      previousNavDate: window[0]?.previousNavDate ?? null,
    }];
  })) as Record<PerformancePeriod, VolatilityMetric>;
}

export function calculateSchemeVolatility(input: SchemeSeries, asOfDate?: string) {
  return volatilityForPrepared(prepareScheme(input, asOfDate));
}

export function calculateAllSchemesVolatility(schemes: SchemeSeries[], asOfDate?: string) {
  return schemes.map((input) => ({ scheme: input.scheme, periods: calculateSchemeVolatility(input, asOfDate) }));
}
