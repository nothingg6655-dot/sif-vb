import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { calculateAbsoluteReturn, calculateSchemeAbsoluteReturns } from '../src/services/absoluteReturns.service.js';
import { calculateDailyReturns, calculateSampleStandardDeviation, calculateAnnualizedVolatility,
  calculateSchemeVolatility } from '../src/services/volatility.service.js';
import { computeSchemePerformance } from '../src/services/scheme-performance-calculator.js';
import { displayDecimal, periodTarget, serializeSchemePerformance, type SchemeSeries } from '../src/services/scheme-performance-model.js';

function series(points: Array<[string, unknown]>, code = '1'): SchemeSeries {
  return { scheme: { schemeId: `TEST:${code}`, source: 'TEST', schemeCode: code,
    externalSchemeId: null, schemeName: 'Unit test scheme', plan: 'Regular', option: 'Growth', amc: null },
  observations: points.map(([date, nav]) => ({ date, nav })) };
}

describe('absolute returns and calendar boundaries', () => {
  const input = series([
    ['2025-10-01', '80'], ['2025-11-06', '90'], ['2025-11-07', '100'],
    ['2026-05-06', '110'], ['2026-05-07', '120'], ['2026-08-06', '130'],
    ['2026-08-07', '140'], ['2026-10-06', '150'], ['2026-10-07', '160'],
    ['2026-11-06', '200'], ['2026-11-09', '220'],
  ]);
  it.each([['1M', 25, '2026-10-07'], ['3M', 42.86, '2026-08-07'],
    ['6M', 66.67, '2026-05-07'], ['1Y', 100, '2025-11-07'], ['SI', 150, '2025-10-01']] as const)(
    'calculates %s using the first valid NAV strictly after its target', (period, expected, start) => {
      const result = calculateSchemeAbsoluteReturns(input, '2026-11-06')[period];
      expect(displayDecimal(result.absoluteReturn)).toBe(expected);
      expect(result.startingNavDate).toBe(start);
    });
  it('selects the first available NAV when the target is a non-trading day', () => {
    const result = calculateSchemeAbsoluteReturns(series([
      ['2026-10-02', '100'], ['2026-10-08', '110'], ['2026-10-09', '120'], ['2026-11-06', '132'],
    ]), '2026-11-06')['1M'];
    expect(result.startingNavDate).toBe('2026-10-08');
    expect(displayDecimal(result.absoluteReturn)).toBe(20);
  });
  it('uses each scheme latest NAV by default and respects historical cutoffs', () => {
    expect(computeSchemePerformance(input).asOfDate).toBe('2026-11-09');
    expect(computeSchemePerformance(input, '2026-11-08').currentNavDate).toBe('2026-11-06');
    expect(computeSchemePerformance(input, '2026-11-08').periods['1M'].targetDate).toBe('2026-10-08');
  });
  it.each([['2026-03-31', '1M', '2026-02-28'], ['2024-03-31', '1M', '2024-02-29'],
    ['2024-02-29', '1Y', '2023-02-28'], ['2026-01-31', '3M', '2025-10-31']] as const)(
    'clamps %s minus %s to %s', (date, period, expected) => expect(periodTarget(period, date)).toBe(expected));
  it('keeps precision through arithmetic and preserves real zero returns', () => {
    expect(calculateAbsoluteReturn('1.0000000001', '1.0000000002')!.gt(0)).toBe(true);
    expect(calculateAbsoluteReturn('100', '100')!.toString()).toBe('0');
    expect(calculateAbsoluteReturn('0', '100')).toBeNull();
  });
  it('returns null when there is no valid observation in the window or no elapsed period', () => {
    expect(calculateSchemeAbsoluteReturns(series([['2025-01-01', '100'], ['2025-01-02', '101']]), '2026-11-06')['1M'].absoluteReturn).toBeNull();
    expect(calculateSchemeAbsoluteReturns(series([['2026-11-06', '100']]))['SI'].absoluteReturn).toBeNull();
    expect(computeSchemePerformance(series([])).currentNav).toBeNull();
  });
  it('uses actual available history without inventing a target-date NAV for a young scheme', () => {
    const result = calculateSchemeAbsoluteReturns(series([['2026-10-15', '100'], ['2026-11-06', '110']]), '2026-11-06');
    expect(result['1M'].startingNavDate).toBe('2026-10-15');
    expect(displayDecimal(result['1M'].absoluteReturn)).toBe(10);
  });
  it('rejects conflicting duplicates while deduplicating identical NAVs', () => {
    expect(() => computeSchemePerformance(series([['2026-11-05', '100'], ['2026-11-05', '101']]))).toThrow('Conflicting');
    expect(computeSchemePerformance(series([['2026-11-05', '100'], ['2026-11-05', '100.00']])).warnings).toContain('duplicate_nav_deduplicated');
  });
});

describe('sample volatility of consecutive valid daily returns', () => {
  it('uses decimal returns, sample variance, and sqrt(252)', () => {
    const daily = calculateDailyReturns([{ date: '2026-11-01', nav: '100' },
      { date: '2026-11-02', nav: '110' }, { date: '2026-11-03', nav: '99' }]);
    expect(daily.map((item) => item.value.toNumber())).toEqual([0.1, -0.1]);
    expect(calculateSampleStandardDeviation(daily.map((item) => item.value))!.toNumber()).toBeCloseTo(Math.sqrt(0.02), 14);
    expect(displayDecimal(calculateAnnualizedVolatility(daily.map((item) => item.value)))).toBe(224.5);
  });
  it('includes the return just after the target using a NAV on or before the target as its base', () => {
    const input = series([['2026-10-05', '100'], ['2026-10-06', '110'], ['2026-10-07', '99'], ['2026-11-06', '108.9']]);
    const result = calculateSchemeVolatility(input, '2026-11-06')['1M'];
    expect(result.observationCount).toBe(2);
    expect(result.previousNavDate).toBe('2026-10-06');
    expect(result.firstReturnDate).toBe('2026-10-07');
    expect(displayDecimal(result.annualizedVolatility)).toBe(224.5);
  });
  it.each(['1M', '3M', '6M', '1Y', 'SI'] as const)('calculates %s with at least two actual return observations', (period) => {
    const result = calculateSchemeVolatility(series([
      ['2026-11-02', '100'], ['2026-11-04', '110'], ['2026-11-06', '99'],
    ]), '2026-11-06')[period];
    expect(result.observationCount).toBe(2);
    expect(displayDecimal(result.annualizedVolatility)).toBe(224.5);
  });
  it('does not fill dates or reject a long gap and sorts observations before pairing', () => {
    const daily = calculateDailyReturns([{ date: '2026-11-06', nav: '121' },
      { date: '2026-09-01', nav: '100' }, { date: '2026-10-01', nav: '110' }]);
    expect(daily.length).toBe(2);
    expect(daily[0]!.value.toNumber()).toBe(0.1);
    expect(displayDecimal(calculateAnnualizedVolatility(daily.map((item) => item.value)))).toBe(0);
  });
  it('excludes zero, negative, null, nonfinite, invalid-date and nonnumeric NAVs', () => {
    const input = series([['2026-11-01', '100'], ['2026-11-02', 0], ['2026-11-03', '-10'],
      ['2026-11-04', null], ['2026-11-05', 'NaN'], ['2026-11-06', '110'],
      ['2026-11-07', 'Infinity'], ['2026-11-08', 'bad'], ['invalid', '1000'], ['2026-11-09', '99']]);
    const result = computeSchemePerformance(input);
    expect(result.periods.SI.observationCount).toBe(2);
    expect(displayDecimal(result.periods.SI.annualizedVolatility)).toBe(224.5);
  });
  it('returns null for fewer than two returns and zero for an observed constant NAV series', () => {
    expect(calculateAnnualizedVolatility([])).toBeNull();
    expect(calculateAnnualizedVolatility(['0.01'])).toBeNull();
    expect(calculateAnnualizedVolatility(['0', '0'])!.toString()).toBe('0');
  });
  it('keeps all plan and option variants independent', () => {
    const a = computeSchemePerformance(series([['2026-11-01', '100'], ['2026-11-02', '110'], ['2026-11-03', '99']], '1'));
    const b = computeSchemePerformance(series([['2026-11-01', '200'], ['2026-11-02', '200'], ['2026-11-03', '200']], '2'));
    expect(displayDecimal(a.periods.SI.annualizedVolatility)).toBe(224.5);
    expect(displayDecimal(b.periods.SI.annualizedVolatility)).toBe(0);
    expect(a.sourceFingerprint).not.toBe(b.sourceFingerprint);
  });
});

describe('real CSV hybrid regression fixtures', () => {
  const fixture = JSON.parse(readFileSync(new URL('./fixtures/scheme-performance-reference-2026-11-06.json', import.meta.url), 'utf8'));
  it.each(fixture.schemes as Array<{ scheme: SchemeSeries['scheme']; observations: SchemeSeries['observations']; expected: Record<string, {
    absoluteReturn: number | null; annualizedVolatility: number | null; startingNavDate: string | null; observationCount: number;
  }> }>)('matches independent CSV calculations for $scheme.schemeName', (input) => {
    const result = serializeSchemePerformance(computeSchemePerformance(input, fixture.asOfDate));
    for (const [period, expected] of Object.entries(input.expected)) {
      expect(result.periods[period]).toMatchObject(expected);
    }
  });
});
