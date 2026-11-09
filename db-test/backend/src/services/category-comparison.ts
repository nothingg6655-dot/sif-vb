import type pg from 'pg';
import { readComparisonMetadata, type ComparisonMetadata } from '../repositories/category-comparison.js';
import { readPerformanceNav, type PreferredNavRow } from '../repositories/funds.js';
import { calculateUnifiedPerformance, type ReturnMetric } from './analytics.js';
import { groupSeries, type NavObservation } from './performance.js';
import { calculateDailyReturns, calculateAnnualizedVolatility } from './volatility.service.js';
import { displayDecimal } from './scheme-performance-model.js';

export const comparisonPeriods = [
  { id: 'r1m', label: '1 Month' }, { id: 'r3m', label: '3 Months' },
  { id: 'r6m', label: '6 Months' }, { id: 'r1y', label: '1 Year' },
  { id: 'si', label: 'Since Inception' },
] as const;

const categories = [
  { id: 'equity', label: 'Equity', strategy: 'Equity Long-Short', title: 'Equity Long Short' },
  { id: 'ex-top-100', label: 'Equity Ex-top 100', strategy: 'Equity Ex-Top 100', title: 'Equity Ex-top 100 Long Short' },
  { id: 'hybrid', label: 'Hybrid', strategy: 'Hybrid Long-Short', title: 'Hybrid Long Short' },
  { id: 'asset-allocator', label: 'Active asset allocator', strategy: 'Active Asset Allocator', title: 'Active Asset Allocator Long Short' },
  { id: 'sector-rotation', label: 'Sector Rotation', strategy: 'Sector Rotation', title: 'Sector Rotation Long Short' },
  { id: 'debt', label: 'Debt', strategy: 'Debt Long-Short', title: 'Debt Long Short' },
];

export interface VolatilityMetric {
  value: number | null; observations: number; startDate: string | null;
  endDate: string | null; status: string;
}

// Filter consecutive returns by their return date, preserving the preceding NAV
// even when it falls on or before the requested window boundary.
export function annualizedVolatility(
  observations: NavObservation[], metric: ReturnMetric | undefined,
): VolatilityMetric {
  const missing: VolatilityMetric = {
    value: null, observations: 0, startDate: metric?.startDate ?? null,
    endDate: metric?.endDate ?? null, status: metric?.status ?? 'unavailable',
  };
  if (!metric?.endDate || metric.status === 'invalid') return missing;
  try {
    const daily = calculateDailyReturns(observations.filter((row) => row.date <= metric.endDate!))
      .filter((row) => !metric.requestedStartDate || row.date > metric.requestedStartDate);
    return {
      value: displayDecimal(calculateAnnualizedVolatility(daily.map((row) => row.value))),
      observations: daily.length, startDate: daily[0]?.date ?? null, endDate: metric.endDate,
      status: daily.length >= 2 ? 'complete' : 'insufficient-history',
    };
  } catch { return { ...missing, status: 'invalid' }; }
}

function categoryId(row: ComparisonMetadata): string {
  return categories.find((item) => item.strategy === row.strategy)?.id
    ?? (row.category === 'Debt' ? 'debt' : 'other');
}

export function calculateCategoryComparison(
  rows: PreferredNavRow[], metadata: ComparisonMetadata[], toDate: string, requestedAsOfDate: string | null = toDate,
) {
  const analytics = calculateUnifiedPerformance(rows, toDate, requestedAsOfDate);
  const performance = new Map(analytics.funds.map((fund) => [fund.fundId, fund]));
  const series = new Map(groupSeries(rows, toDate).map((fund) => [fund.fundId, fund.observations]));
  const funds = metadata.map((row) => {
    const fund = performance.get(row.fundId);
    const band = row.riskBand?.trim() || null;
    const match = band?.match(/^(?:risk\s*band\s*)?(?:level\s*)?([1-5])$/i);
    const amount = row.netAumCr === null ? null : Number(row.netAumCr);
    return {
      fundId: row.fundId, id: fund?.id ?? null,
      latestNav: fund?.latestNav ?? null,
      fullName: row.fullName, slug: row.slug, amc: row.amc,
      categoryId: categoryId(row), category: row.category, strategy: row.strategy,
      launchDate: row.launchDate, inceptionDate: fund?.inceptionDate ?? null,
      plan: 'Regular', option: 'Growth', latestDate: fund?.latestDate ?? null,
      asOfDate: fund?.asOfDate ?? null,
      netAumCr: amount !== null && Number.isFinite(amount) && amount >= 0 ? amount : null,
      aumAsOf: row.aumAsOf, riskBand: band, riskLevel: match ? Number(match[1]) : null,
      riskAsOf: row.riskAsOf, returnProfile: row.returnProfile, pdfUrl: row.pdfUrl,
      returns: Object.fromEntries(comparisonPeriods.map(({ id }) => [id, fund?.returns[id] ?? null])),
      volatility: Object.fromEntries(comparisonPeriods.map(({ id }) => [
        id, annualizedVolatility(series.get(row.fundId) ?? [], fund?.returns[id]),
      ])),
    };
  });
  const definitions = funds.some((fund) => fund.categoryId === 'other')
    ? [...categories, { id: 'other', label: 'Other', strategy: '', title: 'Other' }] : categories;
  return {
    success: true, asOfDate: toDate, plan: 'Regular', option: 'Growth',
    // Platform asset classes include categories with no active strategies yet.
    platformCategories: ['Equity', 'Debt', 'Hybrid'],
    periods: comparisonPeriods,
    categories: definitions.map(({ strategy: _, ...item }) => ({
      ...item, fundCount: funds.filter((fund) => fund.categoryId === item.id).length,
    })),
    methodology: {
      returns: 'Absolute NAV returns; subtract 1, 3, 6 calendar months or 1 calendar year and select the first valid NAV strictly after the target. Since inception uses the first valid scheme NAV.',
      volatility: 'Sample standard deviation of consecutive valid decimal NAV returns × √252 × 100. Return dates must be strictly after the target; at least two returns required. Missing dates are not filled.',
      aum: 'Published net AUM from the latest verified official factsheet up to the requested date; INR in crore. Quarterly average AUM is not substituted.',
      inception: 'Published launch date when available; otherwise first recorded NAV date.',
    },
    funds,
  };
}

export async function categoryComparison(pool: pg.Pool, toDate: string, requestedAsOfDate: string | null = toDate) {
  const [rows, metadata] = await Promise.all([
    readPerformanceNav(pool, toDate), readComparisonMetadata(pool, toDate),
  ]);
  return calculateCategoryComparison(rows, metadata, toDate, requestedAsOfDate);
}
