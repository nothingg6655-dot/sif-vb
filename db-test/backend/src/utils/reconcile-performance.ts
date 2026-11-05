import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createPool } from '../config/database.js';
import { readConfig } from '../config/env.js';
import { readPerformanceNav, type PreferredNavRow } from '../repositories/funds.js';
import {
  calculateSchemePerformance, percentageReturn,
  SIF360_OBSERVED_POLICY, type PerformanceCalculationPolicy,
} from '../services/performance.js';

const toDate = '2026-10-14';
const from1m = '2026-09-14';
const from3m = '2026-07-16';
const reportDirectory = fileURLToPath(new URL('../../../docs/reports/', import.meta.url));
const fixturePath = fileURLToPath(new URL('../../tests/fixtures/sif360-performance-2026-10-14.json', import.meta.url));
const metrics = ['changePct', 'return1m', 'return3m', 'latestNav', 'latestDate', 'inceptionDate'] as const;

type Metric = typeof metrics[number];
type ReferenceItem = Record<Metric, number | string | null>;

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function displayDate(value: string | null): string | null {
  if (value === null) return null;
  const date = new Date(`${value}T00:00:00.000Z`);
  return `${String(date.getUTCDate()).padStart(2, '0')} ${
    ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][date.getUTCMonth()]
  } ${date.getUTCFullYear()}`;
}

function equal(left: unknown, right: unknown): boolean {
  return left === right;
}

function publicResult(result: ReturnType<typeof calculateSchemePerformance>): ReferenceItem {
  return {
    changePct: result.changePct,
    return1m: result.return1m,
    return3m: result.return3m,
    latestNav: result.latestNav,
    latestDate: displayDate(result.latestDate),
    inceptionDate: displayDate(result.inceptionDate),
  };
}

function groupRows(rows: PreferredNavRow[]) {
  const grouped = new Map<number, PreferredNavRow[]>();
  for (const row of rows) {
    const items = grouped.get(row.displayOrder) ?? [];
    items.push(row);
    grouped.set(row.displayOrder, items);
  }
  return grouped;
}

function calculateGroup(
  rows: PreferredNavRow[], oneMonthTarget: string, threeMonthTarget: string,
  policy: PerformanceCalculationPolicy,
) {
  const first = rows[0]!;
  return calculateSchemePerformance({
    source: first.source, schemeCode: first.schemeCode,
    observations: rows.map(({ date, nav }) => ({ date, nav })),
  }, toDate, oneMonthTarget, threeMonthTarget, policy);
}

async function main() {
  const reference = JSON.parse(await readFile(fixturePath, 'utf8')) as {
    performanceMap: Record<string, ReferenceItem>;
  };
  const pool = createPool(readConfig());
  try {
    const rows = await readPerformanceNav(pool, toDate);
    const grouped = groupRows(rows);
    const mapping = (await pool.query<{
      referenceFundId: number; fundId: number; canonicalName: string;
      source: string; schemeCode: number; schemeName: string; plan: string;
      option: string; isins: string[]; firstNavDate: string; latestNavDate: string;
      observationCount: number; mappingConfidence: string; evidence: object;
    }>(`
      SELECT c.reference_fund_id AS "referenceFundId",f.fund_id::integer AS "fundId",
             f.canonical_name AS "canonicalName",s.source,
             s.scheme_code::integer AS "schemeCode",s.scheme_name AS "schemeName",
             s.scheme_plan AS plan,s.scheme_option AS option,
             array_agg(DISTINCT i.isin ORDER BY i.isin) AS isins,
             min(n.nav_date)::text AS "firstNavDate",
             max(n.nav_date)::text AS "latestNavDate",
             count(DISTINCT n.nav_date)::integer AS "observationCount",
             c.mapping_confidence AS "mappingConfidence",
             c.selection_evidence AS evidence
      FROM public.app_performance_config c
      JOIN public.app_funds f USING(fund_id)
      JOIN public.tigzig_schemes s
        ON (s.source,s.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
      JOIN public.tigzig_scheme_isins i USING(source,scheme_code)
      JOIN public.tigzig_scheme_nav n USING(source,scheme_code)
      GROUP BY c.reference_fund_id,f.fund_id,f.canonical_name,s.source,s.scheme_code,
               s.scheme_name,s.scheme_plan,s.scheme_option,c.mapping_confidence,
               c.selection_evidence
      ORDER BY c.reference_fund_id
    `)).rows;

    const normalized: Record<string, ReturnType<typeof calculateSchemePerformance>> = {};
    const calculated: Record<string, ReferenceItem> = {};
    for (const [referenceId, fundRows] of grouped) {
      const result = calculateGroup(
        fundRows, from1m, from3m, SIF360_OBSERVED_POLICY,
      );
      normalized[String(referenceId)] = result;
      calculated[String(referenceId)] = publicResult(result);
    }

    const reconciliation = [];
    for (let referenceId = 1; referenceId <= 34; referenceId += 1) {
      const expected = reference.performanceMap[String(referenceId)]!;
      const actual = calculated[String(referenceId)]!;
      const scheme = mapping.find((item) => item.referenceFundId === referenceId)!;
      for (const metric of metrics) {
        const expectedValue = expected[metric];
        const actualValue = actual[metric];
        const difference = typeof expectedValue === 'number' && typeof actualValue === 'number'
          ? Math.round((actualValue - expectedValue) * 1e10) / 1e10 : null;
        reconciliation.push({
          fundId: referenceId, schemeCode: scheme.schemeCode, metric,
          sif360Value: expectedValue, calculatedValue: actualValue, difference,
          status: equal(expectedValue, actualValue)
            ? (expectedValue === null ? 'exact-null' : 'exact') : 'mismatch',
        });
      }
    }

    const policy = (oneMonthSelection: PerformanceCalculationPolicy['oneMonthSelection'],
      threeMonthSelection: PerformanceCalculationPolicy['threeMonthSelection']) => ({
      ...SIF360_OBSERVED_POLICY, oneMonthSelection, threeMonthSelection,
    });
    const matchRate = (
      metric: 'return1m' | 'return3m', oneMonthTarget: string, threeMonthTarget: string,
      candidatePolicy: PerformanceCalculationPolicy,
    ) => {
      let matches = 0;
      for (const [referenceId, fundRows] of grouped) {
        const result = calculateGroup(
          fundRows, oneMonthTarget, threeMonthTarget, candidatePolicy,
        );
        if (result[metric] === reference.performanceMap[String(referenceId)]![metric]) matches += 1;
      }
      return matches;
    };
    const fixedObservationBest = (metric: 'return1m' | 'return3m', range: number[]) => {
      let best = { observationIntervals: 0, matches: -1 };
      for (const intervals of range) {
        let matches = 0;
        for (const [referenceId, fundRows] of grouped) {
          const ordered = [...fundRows].sort((left, right) => left.date.localeCompare(right.date));
          const latest = ordered.at(-1)!;
          const start = ordered[Math.max(0, ordered.length - 1 - intervals)]!;
          const value = percentageReturn(start.nav, latest.nav, true).value;
          if (value === reference.performanceMap[String(referenceId)]![metric]) matches += 1;
        }
        if (matches > best.matches) best = { observationIntervals: intervals, matches };
      }
      return best;
    };
    const candidateResults = [
      { metric: 'return1m', method: '30/calendar days; latest NAV on/before 2026-09-14', matches: matchRate('return1m', from1m, from3m, policy('on-or-before', 'on-or-after')) },
      { metric: 'return1m', method: '30/calendar days; first NAV on/after 2026-09-14', matches: matchRate('return1m', from1m, from3m, policy('on-or-after', 'on-or-after')) },
      { metric: 'return1m', method: '30/calendar days; first NAV strictly after 2026-09-14', matches: matchRate('return1m', from1m, from3m, policy('strictly-after', 'on-or-after')) },
      { metric: 'return3m', method: '3 calendar months; first NAV on/after 2026-07-14', matches: matchRate('return3m', from1m, '2026-07-14', policy('on-or-after', 'on-or-after')) },
      { metric: 'return3m', method: '90 days; latest NAV on/before 2026-07-16', matches: matchRate('return3m', from1m, from3m, policy('on-or-after', 'on-or-before')) },
      { metric: 'return3m', method: '90 days; first NAV on/after 2026-07-16', matches: matchRate('return3m', from1m, from3m, policy('on-or-after', 'on-or-after')) },
      { metric: 'return3m', method: '90 days; first NAV strictly after 2026-07-16', matches: matchRate('return3m', from1m, from3m, policy('on-or-after', 'strictly-after')) },
    ];
    const fixed1m = fixedObservationBest('return1m', [18, 19, 20, 21, 22, 23, 24]);
    const fixed3m = fixedObservationBest('return3m', [60, 61, 62, 63, 64, 65, 66, 67, 68]);
    candidateResults.push(
      { metric: 'return1m', method: `best fixed observation interval (${fixed1m.observationIntervals})`, matches: fixed1m.matches },
      { metric: 'return3m', method: `best fixed observation interval (${fixed3m.observationIntervals})`, matches: fixed3m.matches },
    );

    const firstFund = normalized['1']!;
    const firstRows = grouped.get(1)!;
    const latest = firstRows.find((item) => item.date === firstFund.latestDate)!;
    const inception = firstRows.find((item) => item.date === firstFund.inceptionDate)!;
    const oneMonth = firstRows.find((item) => item.date === firstFund.referenceDate1m)!;
    const threeMonth = firstRows.find((item) => item.date === firstFund.referenceDate3m)!;
    const regressionCase = {
      referenceFundId: 1,
      schemeCode: 13,
      latest: { date: latest.date, nav: Number(latest.nav) },
      changePct: {
        targetDate: inception.date, selectedDate: inception.date,
        selectedNav: Number(inception.nav),
        ...percentageReturn(inception.nav, latest.nav, true),
      },
      return1m: {
        targetDate: from1m, selectedDate: oneMonth.date,
        selectedNav: Number(oneMonth.nav),
        ...percentageReturn(oneMonth.nav, latest.nav, true),
      },
      return3m: {
        targetDate: from3m, selectedDate: threeMonth.date,
        selectedNav: Number(threeMonth.nav),
        ...percentageReturn(threeMonth.nav, latest.nav, true),
      },
    };

    const summary = reconciliation.reduce((counts, row) => {
      counts[row.status] = (counts[row.status] ?? 0) + 1;
      return counts;
    }, {} as Record<string, number>);
    const report = {
      generatedAt: new Date().toISOString(),
      source: {
        csv: 'data/tigzig/sif_nav.csv',
        sha256: '7c715ec1fe87c0c8c8dfe77ce06fd9c957c62fe739d2e20669aa226a689e11b4',
        inputRows: 15620, validDatabaseRows: 15616, schemes: 126,
        duplicateBusinessKeys: 0, malformedRows: 0, nonPositiveRowsRejected: 4,
        dateRange: ['2025-10-08', '2026-10-13'],
      },
      request: { toDate, from1m, from3m },
      methodology: {
        selected: SIF360_OBSERVED_POLICY,
        oneMonthBoundaryUncertainty: 'No NAV exists on 2026-09-14, so on-or-after and strictly-after cannot be distinguished by this fixture.',
        threeMonthBoundaryUncertainty: 'A NAV exists on 2026-07-16, so on-or-before and on-or-after cannot be distinguished by this fixture; strictly-after is rejected.',
        candidateResults: candidateResults.map((item) => ({ ...item, total: 34 })),
      },
      mapping,
      regressionCase,
      reconciliationSummary: { totalMetrics: reconciliation.length, ...summary },
      unresolved: [
        { referenceFundId: 12, reason: 'TIGZIG Regular Growth series ends 2026-10-12; reference has 2026-10-13.' },
        { referenceFundId: 25, reason: 'TIGZIG Regular Growth series ends 2026-10-12; reference has 2026-10-13.' },
      ],
      normalized,
      reconciliation,
      sampleApiResponse: { success: true, isBadData: false, performanceMap: calculated },
    };

    await mkdir(reportDirectory, { recursive: true });
    await writeFile(`${reportDirectory}/sif360-performance-reconciliation.json`,
      `${JSON.stringify(report, null, 2)}\n`, 'utf8');
    const mappingHeader = ['Fund ID','Scheme Code','Scheme Name','Plan','Option','ISINs','First NAV Date','Latest NAV Date','Observations','Mapping Confidence','Ambiguity'];
    const mappingCsv = [mappingHeader, ...mapping.map((item) => [
      item.referenceFundId,item.schemeCode,item.schemeName,item.plan,item.option,
      item.isins.join('|'),item.firstNavDate,item.latestNavDate,item.observationCount,
      item.mappingConfidence,'none',
    ])].map((line) => line.map(csvCell).join(',')).join('\n');
    await writeFile(`${reportDirectory}/sif360-performance-mapping.csv`, `${mappingCsv}\n`, 'utf8');
    const reconciliationHeader = ['Fund ID','Scheme Code','Metric','SIF360 Value','Calculated Value','Difference','Status'];
    const reconciliationCsv = [reconciliationHeader, ...reconciliation.map((item) => [
      item.fundId,item.schemeCode,item.metric,item.sif360Value,item.calculatedValue,
      item.difference,item.status,
    ])].map((line) => line.map(csvCell).join(',')).join('\n');
    await writeFile(`${reportDirectory}/sif360-performance-reconciliation.csv`,
      `${reconciliationCsv}\n`, 'utf8');
    console.log(JSON.stringify({ mappingCount: mapping.length, summary, candidateResults }, null, 2));
  } finally {
    await pool.end();
  }
}

await main();
