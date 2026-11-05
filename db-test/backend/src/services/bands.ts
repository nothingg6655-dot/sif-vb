import type pg from 'pg';
import { readRiskBandByName } from '../repositories/bands.js';
import { normalizeSchemeName } from './allocations.js';

function explicitLevel(label: string | null): number | null {
  // Parse only an explicitly published level; prose risk labels have no inferred number.
  const match = label?.match(/^(?:risk\s*band\s*)?(?:level\s*)?([1-5])$/i);
  return match ? Number(match[1]) : null;
}

export async function riskBandByName(pool: pg.Pool, schemeName: string) {
  const rows = await readRiskBandByName(pool, normalizeSchemeName(schemeName));
  const report = rows[0];
  if (!report) return null;
  if (rows.some((row) => row.strategyId !== report.strategyId)) return 'ambiguous' as const;
  if (report.reportId === null) return 'factsheet-not-found' as const;

  const strategyBand = report.investmentStrategyRiskBand?.trim() || null;
  const benchmarkBand = report.benchmarkRiskBand?.trim() || null;
  return {
    success: true,
    data: {
      strategyId: report.strategyId, fullName: report.fullName, slug: report.slug,
      reportId: report.reportId, reportMonth: report.reportMonth, portfolioAsOf: report.portfolioAsOf,
      riskBandAsOf: report.riskBandAsOf,
      availability: strategyBand && benchmarkBand ? 'PUBLISHED'
        : strategyBand || benchmarkBand ? 'PARTIAL' : 'UNKNOWN',
      investmentStrategyRiskBand: strategyBand,
      investmentStrategyRiskLevel: explicitLevel(strategyBand),
      benchmarkRiskBand: benchmarkBand,
      benchmarkRiskLevel: explicitLevel(benchmarkBand),
      benchmarkName: report.benchmarkName,
      source: {
        pdfUrl: report.pdfUrl, validationStatus: report.validationStatus,
        latestListingVerified: report.latestListingVerified,
      },
    },
  };
}
