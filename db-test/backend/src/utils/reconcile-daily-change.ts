import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createPool } from '../config/database.js';
import { readConfig } from '../config/env.js';
import { readLiveTicker } from '../repositories/funds.js';
import { calculateDailyTicker } from '../services/daily-change.js';

interface ReferenceItem { fullName: string; latestNAV: number; change: number }
interface ReferenceFixture { data: ReferenceItem[] }

function csvCell(value: unknown): string {
  if (value === null || value === undefined) return '';
  const text = Array.isArray(value) ? value.join('|') : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function sameNumber(left: number | null, right: number): boolean {
  return left !== null && Math.abs(left - right) < 1e-12;
}

async function main() {
  const fixturePath = fileURLToPath(new URL(
    '../../tests/fixtures/sif360-live-ticker-2026-10-14.json', import.meta.url,
  ));
  const reportJsonPath = fileURLToPath(new URL(
    '../../../docs/reports/sif360-daily-change-reconciliation.json', import.meta.url,
  ));
  const reportCsvPath = fileURLToPath(new URL(
    '../../../docs/reports/sif360-daily-change-reconciliation.csv', import.meta.url,
  ));
  const fixture = JSON.parse(
    readFileSync(fixturePath, 'utf8').replace(/^\uFEFF/, ''),
  ) as ReferenceFixture;
  const pool = createPool(readConfig());
  try {
    const calculated = calculateDailyTicker(await readLiveTicker(pool));
    if (fixture.data.length !== calculated.data.length) {
      throw new Error(`Fund count differs: reference=${fixture.data.length}, local=${calculated.data.length}`);
    }
    const rows = fixture.data.map((expected, index) => {
      const actual = calculated.data[index]!;
      const diagnostic = calculated.diagnostics[index]!;
      const navMatch = sameNumber(actual.latestNAV, expected.latestNAV);
      const changeMatch = sameNumber(actual.change, expected.change);
      const nameMatch = actual.fullName === expected.fullName;
      const sourceDataLag = !navMatch && diagnostic.latestDate !== null
        && diagnostic.latestDate < '2026-10-13';
      return {
        referenceFundId: index + 1,
        expectedName: expected.fullName,
        localName: actual.fullName,
        source: diagnostic.source,
        schemeCode: diagnostic.schemeCode,
        plan: diagnostic.plan,
        option: diagnostic.option,
        currentDate: diagnostic.latestDate,
        currentNav: diagnostic.latestNav,
        previousDate: diagnostic.previousDate,
        previousNav: diagnostic.previousNav,
        expectedLatestNav: expected.latestNAV,
        expectedChange: expected.change,
        calculatedChange: actual.change,
        navMatch,
        changeMatch,
        nameMatch,
        status: navMatch && changeMatch ? (nameMatch ? 'exact' : 'financial-exact-name-alias')
          : sourceDataLag ? 'source-data-lag' : 'mismatch',
        dataQualityWarnings: diagnostic.dataQualityWarnings,
      };
    });
    const summary = {
      funds: rows.length,
      financialExact: rows.filter((row) => row.navMatch && row.changeMatch).length,
      financialMismatch: rows.filter((row) => !row.navMatch || !row.changeMatch).length,
      nameExact: rows.filter((row) => row.nameMatch).length,
      nameAlias: rows.filter((row) => !row.nameMatch).length,
      sourceDataLag: rows.filter((row) => row.status === 'source-data-lag').length,
    };
    const report = {
      generatedAt: new Date().toISOString(),
      reference: 'https://www.sif360.com/api/liveTicker',
      fixture: 'backend/tests/fixtures/sif360-live-ticker-2026-10-14.json',
      formula: '((latestNAV / previousNAV) - 1) * 100, rounded half-up to 2 decimals',
      summary,
      rows,
      sampleApiResponse: { data: calculated.data.slice(0, 3) },
    };
    mkdirSync(dirname(reportJsonPath), { recursive: true });
    writeFileSync(reportJsonPath, `${JSON.stringify(report, null, 2)}\n`);
    const columns = [
      'referenceFundId', 'expectedName', 'localName', 'source', 'schemeCode', 'plan',
      'option', 'currentDate', 'currentNav', 'previousDate', 'previousNav',
      'expectedLatestNav', 'expectedChange', 'calculatedChange', 'navMatch',
      'changeMatch', 'nameMatch', 'status', 'dataQualityWarnings',
    ] as const;
    const csv = [columns.join(','), ...rows.map((row) =>
      columns.map((column) => csvCell(row[column])).join(','))].join('\n');
    writeFileSync(reportCsvPath, `${csv}\n`);
    process.stdout.write(`${JSON.stringify({ summary, reportJsonPath, reportCsvPath }, null, 2)}\n`);
  } finally {
    await pool.end();
  }
}

main().catch((error: unknown) => {
  process.stderr.write(`Daily-change reconciliation failed: ${
    error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
