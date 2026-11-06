import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createPool } from '../src/config/database.js';
import { readConfig } from '../src/config/env.js';
import { fundDetails } from '../src/services/funds.js';

test('fund details integrate published Regular expenses and one current manager roster', async () => {
  const pool = createPool(readConfig());
  try {
    const result = await fundDetails(pool, 'altiva-equity-ex-top-100-long-short-fund');
    assert.ok(result);
    assert.equal(result.data.expenseRatio.available, true);
    assert.equal(result.data.profile?.expenseRatioType, 'BASE');
    assert.equal(typeof result.data.profile?.expenseRatioPercent, 'number');
    assert.ok(result.data.profile?.benchmarkName);
    const roster = await pool.query(`SELECT fm.manager_name AS name,rfm.manager_role AS role,
      rfm.managing_since::text AS "managingSince"
      FROM sif_report_fund_managers rfm JOIN sif_fund_managers fm USING(manager_id)
      WHERE rfm.report_id=$1 ORDER BY fm.manager_name,rfm.manager_role`,
    [result.data.profile?.reportId]);
    assert.deepEqual(result.data.managers, roster.rows);
    assert.ok(result.data.schemes.every((scheme) => scheme.plan === 'Regular'));
  } finally { await pool.end(); }
});

test('fund details reject reports whose document belongs to a different AMC', async () => {
  const pool = createPool(readConfig());
  try {
    for (const slug of ['altiva-equity-long-short-fund', 'apex-equity-long-short-fund']) {
      const result = await fundDetails(pool, slug);
      assert.ok(result);
      if (result.data.profile) {
        const source = await pool.query(`SELECT st.amc_id=d.amc_id AS verified
          FROM sif_factsheet_reports r JOIN sif_strategies st USING(strategy_id)
          JOIN sif_factsheet_documents d USING(document_id) WHERE r.report_id=$1`,
        [result.data.profile.reportId]);
        assert.equal(source.rows[0].verified, true);
      } else {
        assert.deepEqual(result.data.managers, []);
        assert.equal(result.data.expenseRatio.available, false);
      }
    }
  } finally { await pool.end(); }
});
