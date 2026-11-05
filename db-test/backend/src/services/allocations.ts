import type pg from 'pg';
import {
  readFundAllocation, readFundAllocationByName, type AllocationFamily, type AllocationRow,
} from '../repositories/allocations.js';

export const allocationDefinitions = {
  allocation: { family: 'ASSET', field: 'Asset Allocation (%)' },
  'market-cap': { family: 'MARKET_CAP', field: 'Market Capitalization (%)' },
  'sector-allocation': { family: 'SECTOR', field: 'Sector Allocation (%)' },
  'rating-allocation': { family: 'RATING', field: 'Rating Profile (%)' },
} as const satisfies Record<string, { family: AllocationFamily; field: string }>;

export type AllocationType = keyof typeof allocationDefinitions;

export function normalizeSchemeName(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, '');
}

export async function fundAllocation(pool: pg.Pool, slug: string, type: AllocationType) {
  const definition = allocationDefinitions[type];
  const rows = await readFundAllocation(pool, slug, definition.family, definition.field);
  return singleFundAllocation(rows, type);
}

export async function fundAllocationByName(pool: pg.Pool, schemeName: string, type: AllocationType) {
  const definition = allocationDefinitions[type];
  const rows = await readFundAllocationByName(
    pool, normalizeSchemeName(schemeName), definition.family, definition.field,
  );
  return singleFundAllocation(rows, type);
}

function singleFundAllocation(rows: AllocationRow[], type: AllocationType) {
  const report = rows[0];
  if (!report) return null;
  // Refuse normalized-name or cross-AMC slug collisions rather than choosing a fund.
  if (rows.some((row) => row.strategyId !== report.strategyId)) return 'ambiguous' as const;
  if (report.reportId === null) return 'factsheet-not-found' as const;

  return { success: true, data: formatFundAllocation(report, rows, type) };
}

export async function allFundAllocations(pool: pg.Pool, type: AllocationType) {
  const definition = allocationDefinitions[type];
  // A batch query includes strategies without reports and avoids one query per fund.
  const rows = await readFundAllocation(pool, null, definition.family, definition.field);
  const grouped = new Map<number, AllocationRow[]>();
  for (const row of rows) {
    const group = grouped.get(row.strategyId) ?? [];
    group.push(row);
    grouped.set(row.strategyId, group);
  }
  const data = [...grouped.values()].map((group) => formatFundAllocation(group[0]!, group, type));
  return { success: true, count: data.length, data };
}

function formatFundAllocation(report: AllocationRow, rows: AllocationRow[], type: AllocationType) {
  const items = rows.filter((row) => row.label !== null).map((row) => {
    const percent = row.percent === null ? null : Number(row.percent);
    if (percent !== null && !Number.isFinite(percent)) {
      throw new Error('Database returned non-finite allocation data');
    }
    return {
      label: row.label!, percent, basis: row.basis,
      sourcePage: row.sourcePage, publishedOrder: row.publishedOrder,
    };
  });
  // Never add gross and net measures together or treat a missing percentage as zero.
  const completeSingleBasis = items.length > 0 && items.every((item) => item.percent !== null)
    && new Set(items.map((item) => item.basis)).size === 1;
  const totalPercent = completeSingleBasis
    ? Number(items.reduce((total, item) => total + item.percent!, 0).toFixed(8))
    : null;

  return {
    strategyId: report.strategyId, fullName: report.fullName, slug: report.slug,
    allocationType: type, reportId: report.reportId,
    reportMonth: report.reportMonth, portfolioAsOf: report.portfolioAsOf,
    availability: report.reportId === null ? 'MISSING'
      : report.availability ?? (items.length ? 'PUBLISHED' : 'UNKNOWN'),
    unit: 'percent', totalPercent, items,
    source: {
      pdfUrl: report.pdfUrl, validationStatus: report.validationStatus,
      latestListingVerified: report.latestListingVerified,
    },
  };
}
