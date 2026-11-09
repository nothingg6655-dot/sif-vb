import type { Decimal } from 'decimal.js';
import {
  performancePeriods, periodTarget, positiveNav, prepareScheme,
  type AbsoluteMetric, type PerformancePeriod, type PreparedScheme, type SchemeSeries,
} from './scheme-performance-model.js';

export function calculateAbsoluteReturn(startingNav: unknown, currentNav: unknown): Decimal | null {
  const start = positiveNav(startingNav);
  const current = positiveNav(currentNav);
  return start && current ? current.div(start).minus(1).times(100) : null;
}

export function absoluteReturnsForPrepared(input: PreparedScheme): Record<PerformancePeriod, AbsoluteMetric> {
  return Object.fromEntries(performancePeriods.map((period) => {
    const targetDate = periodTarget(period, input.asOfDate);
    const start = period === 'SI' ? input.observations[0]
      : input.observations.find((row) => targetDate !== null && row.date > targetDate);
    const sufficient = start && input.current && start.date < input.current.date;
    return [period, {
      targetDate, startingNav: start?.nav ?? null, startingNavDate: start?.date ?? null,
      absoluteReturn: sufficient ? calculateAbsoluteReturn(start.nav, input.current!.nav) : null,
      status: sufficient ? 'complete' : 'insufficient-history',
    }];
  })) as Record<PerformancePeriod, AbsoluteMetric>;
}

export function calculateSchemeAbsoluteReturns(input: SchemeSeries, asOfDate?: string) {
  return absoluteReturnsForPrepared(prepareScheme(input, asOfDate));
}

export function calculateAllSchemesAbsoluteReturns(schemes: SchemeSeries[], asOfDate?: string) {
  return schemes.map((input) => ({ scheme: input.scheme, periods: calculateSchemeAbsoluteReturns(input, asOfDate) }));
}
