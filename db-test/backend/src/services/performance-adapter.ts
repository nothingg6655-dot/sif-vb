import type { ReturnMetric, TrailingPeriodId } from './analytics.js';
import { displayDecimal, type SchemePerformance, type PerformancePeriod } from './scheme-performance-model.js';

export const corePeriodIds: Partial<Record<TrailingPeriodId, PerformancePeriod>> = {
  r1m: '1M', r3m: '3M', r6m: '6M', r1y: '1Y', si: 'SI',
};

export function legacyReturnMetric(result: SchemePerformance, period: PerformancePeriod): ReturnMetric {
  const metric = result.periods[period];
  return {
    value: displayDecimal(metric.absoluteReturn),
    requestedStartDate: metric.targetDate ?? result.inceptionDate,
    startDate: metric.startingNavDate, startValue: metric.startingNav?.toNumber() ?? null,
    endDate: result.currentNavDate, endValue: result.currentNav?.toNumber() ?? null,
    actualElapsedDays: metric.startingNavDate && result.currentNavDate
      ? Math.round((Date.parse(result.currentNavDate) - Date.parse(metric.startingNavDate)) / 86400000) : null,
    calculationMethod: period === 'SI' ? 'inception-observation' : 'calendar-months:first-strictly-after',
    status: metric.status, warnings: result.warnings,
  };
}
