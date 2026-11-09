import type pg from 'pg';
import { computeSchemePerformance } from './scheme-performance-calculator.js';
import type { SchemePerformance } from './scheme-performance-model.js';
import { persistSchemePerformance, readSchemeNavSeries } from '../repositories/scheme-performance.js';

export class SchemeSelectionError extends Error {
  constructor(public code: 'SCHEME_NOT_FOUND' | 'AMBIGUOUS_SCHEME') { super(code); }
}

export async function calculateAllSchemesPerformance(pool: pg.Pool, options: {
  asOfDate?: string; schemeId?: string; persist?: boolean;
  onError?: (error: { schemeId: string; message: string }) => void;
} = {}) {
  const series = await readSchemeNavSeries(pool, options.asOfDate, options.schemeId);
  if (options.schemeId && series.length !== 1) {
    throw new SchemeSelectionError(series.length ? 'AMBIGUOUS_SCHEME' : 'SCHEME_NOT_FOUND');
  }
  const schemes: SchemePerformance[] = [];
  const errors: Array<{ schemeId: string; message: string }> = [];
  for (const input of series) {
    try { schemes.push(computeSchemePerformance(input, options.asOfDate)); }
    catch (error) {
      const failure = { schemeId: input.scheme.schemeId, message: error instanceof Error ? error.message : 'Calculation failed' };
      errors.push(failure); options.onError?.(failure);
    }
  }
  const persisted = options.persist === false ? { returnRowsChanged: 0, riskRowsChanged: 0 }
    : await persistSchemePerformance(pool, schemes);
  return { schemes, errors, totalSchemes: series.length, ...persisted };
}

export async function calculateSchemePerformance(pool: pg.Pool, schemeId: string, asOfDate?: string) {
  return calculateAllSchemesPerformance(pool, { schemeId, asOfDate });
}
