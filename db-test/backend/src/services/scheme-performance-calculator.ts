import { absoluteReturnsForPrepared } from './absoluteReturns.service.js';
import { volatilityForPrepared } from './volatility.service.js';
import {
  PERFORMANCE_VERSION, performancePeriods, prepareScheme,
  type SchemeSeries, type SchemePerformance,
} from './scheme-performance-model.js';

export function computeSchemePerformance(input: SchemeSeries, asOfDate?: string): SchemePerformance {
  const prepared = prepareScheme(input, asOfDate);
  const returns = absoluteReturnsForPrepared(prepared);
  const volatility = volatilityForPrepared(prepared);
  return {
    scheme: prepared.scheme, asOfDate: prepared.asOfDate,
    currentNav: prepared.current?.nav ?? null, currentNavDate: prepared.current?.date ?? null,
    inceptionDate: prepared.observations[0]?.date ?? null,
    sourceFingerprint: prepared.sourceFingerprint, calculationVersion: PERFORMANCE_VERSION,
    warnings: prepared.warnings,
    periods: Object.fromEntries(performancePeriods.map((period) => [period, {
      ...returns[period], ...volatility[period],
    }])) as SchemePerformance['periods'],
  };
}
