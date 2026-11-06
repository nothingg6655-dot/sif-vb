import assert from 'node:assert/strict';
import { test } from 'node:test';
import Fastify from 'fastify';
import type pg from 'pg';
import { buildApp } from '../src/app.js';
import { readConfig } from '../src/config/env.js';
import { createPool } from '../src/config/database.js';
import { registerRiskBandRoutes } from '../src/routes/bands.js';
import type { RiskBandRow } from '../src/repositories/bands.js';

function url(name: string, path = '/api/band') {
  return `${path}?${new URLSearchParams({ scheme_name: name })}`;
}

test('risk band API resolves every registered and verified plan name to its own stored bands', async () => {
  const config = readConfig({ ...process.env, LOG_LEVEL: 'silent' });
  const app = await buildApp(config);
  const pool = createPool(config);
  try {
    const expected = (await pool.query<{
      strategyId: number; name: string; reportId: number | null;
      strategyBand: string | null; benchmarkBand: string | null;
    }>(`
      WITH names AS (
        SELECT strategy_id,strategy_name AS name FROM public.sif_strategies
        UNION SELECT strategy_id,scheme_name FROM public.vw_sif_verified_strategy_schemes
      )
      SELECT n.strategy_id::integer AS "strategyId",n.name,lr.report_id::integer AS "reportId",
             rb.investment_strategy_risk_band AS "strategyBand",rb.benchmark_risk_band AS "benchmarkBand"
      FROM names n JOIN public.sif_strategies st USING(strategy_id)
      LEFT JOIN LATERAL (
        SELECT r.report_id FROM public.sif_factsheet_reports r
        JOIN public.sif_factsheet_documents d USING(document_id)
        WHERE r.strategy_id=st.strategy_id AND r.is_primary
          AND d.amc_id=st.amc_id AND d.official_only IS TRUE
          AND r.validation_status IN ('PASSED','WARNING')
          AND coalesce(r.portfolio_as_of,r.report_month) <= CURRENT_DATE
        ORDER BY r.report_month DESC,r.extracted_at DESC,r.report_id DESC LIMIT 1
      ) lr ON true
      LEFT JOIN public.sif_report_risk_bands rb ON rb.report_id=lr.report_id
      ORDER BY n.strategy_id,n.name
    `)).rows;
    assert.ok(expected.length >= 34);
    for (const row of expected) {
      const response = await app.inject(url(row.name));
      if (row.reportId === null) {
        assert.equal(response.statusCode, 404, row.name);
        assert.equal(response.json().error.code, 'FACTSHEET_NOT_FOUND');
      } else {
        assert.equal(response.statusCode, 200, `${row.name}: ${response.body}`);
        const { success, data } = response.json();
        assert.equal(success, true);
        assert.equal(data.strategyId, row.strategyId);
        assert.equal(data.reportId, row.reportId);
        assert.equal(data.investmentStrategyRiskBand, row.strategyBand?.trim() || null);
        assert.equal(data.benchmarkRiskBand, row.benchmarkBand?.trim() || null);
        assert.ok(['PASSED', 'WARNING'].includes(data.source.validationStatus));
      }
    }
  } finally { await app.close(); await pool.end(); }
});

test('Dynasif publishes Level 5 for strategy and benchmark with matching route alias and plan name', async () => {
  const app = await buildApp(readConfig({ ...process.env, LOG_LEVEL: 'silent' }));
  try {
    const response = await app.inject(url('DynaSIF Equity Long-Short Fund'));
    assert.equal(response.statusCode, 200);
    const { data } = response.json();
    assert.equal(data.availability, 'PUBLISHED');
    assert.equal(data.investmentStrategyRiskBand, 'Level 5');
    assert.equal(data.investmentStrategyRiskLevel, 5);
    assert.equal(data.benchmarkRiskBand, 'Level 5');
    assert.equal(data.benchmarkRiskLevel, 5);
    assert.equal(data.benchmarkName, 'BSE 500 TRI');
    for (const name of ['  dYnAsIf equity Long – Short Fund ',
      'DynaSIF Equity Long - Short Fund - Direct Plan - Growth Option']) {
      const alias = await app.inject(url(name, '/api/risk-band'));
      assert.equal(alias.statusCode, 200);
      assert.deepEqual(alias.json(), response.json());
    }
    const partial = await app.inject(url('DynaSIF'));
    assert.equal(partial.statusCode, 404);
    assert.equal(partial.json().error.code, 'SCHEME_NOT_FOUND');
  } finally { await app.close(); }
});

const base: RiskBandRow = {
  strategyId: 11, fullName: 'DynaSIF Equity Long-Short Fund', slug: 'dynasif-equity-long-short-fund',
  reportId: 8, reportMonth: '2026-09', portfolioAsOf: '2026-09-30', riskBandAsOf: '2026-08-31',
  investmentStrategyRiskBand: 'Level 5', benchmarkRiskBand: 'Level 5', benchmarkName: 'BSE 500 TRI',
  pdfUrl: null, validationStatus: 'PASSED', latestListingVerified: false,
};

function fixture(rows: RiskBandRow[], failure = false) {
  const calls: unknown[][] = [];
  const pool = {
    async query(_sql: string, values: unknown[]) {
      calls.push(values);
      if (failure) throw new Error('private database details');
      return { rows };
    },
  } as unknown as pg.Pool;
  const app = Fastify({ logger: false });
  registerRiskBandRoutes(app, pool);
  return { app, calls };
}

test('band query validates names before querying and binds the normalized input', async () => {
  const { app, calls } = fixture([base]);
  try {
    for (const path of ['/api/band', '/api/risk-band']) {
      for (const suffix of ['', '?scheme_name=', '?scheme_name=%20%20', '?scheme_name=---',
        '?scheme_name=A&scheme_name=B', `?scheme_name=${'A'.repeat(501)}`]) {
        const response = await app.inject(path + suffix);
        assert.equal(response.statusCode, 400);
        assert.equal(response.json().error.code, 'INVALID_QUERY');
      }
    }
    assert.equal(calls.length, 0);
    assert.equal((await app.inject(url(base.fullName))).statusCode, 200);
    assert.equal((await app.inject(url(base.fullName))).json().data.riskBandAsOf, '2026-08-31');
    assert.deepEqual(calls, [['dynasifequitylongshortfund'], ['dynasifequitylongshortfund']]);
  } finally { await app.close(); }
});

test('band route distinguishes unknown, ambiguous, missing factsheet and database failure', async () => {
  for (const [rows, failure, status, code] of [
    [[], false, 404, 'SCHEME_NOT_FOUND'],
    [[base, { ...base, strategyId: 99 }], false, 409, 'AMBIGUOUS_SCHEME'],
    [[{ ...base, reportId: null }], false, 404, 'FACTSHEET_NOT_FOUND'],
    [[], true, 500, 'BAND_UNAVAILABLE'],
  ] as const) {
    const { app } = fixture([...rows], failure);
    try {
      const response = await app.inject(url(base.fullName));
      assert.equal(response.statusCode, status);
      assert.equal(response.json().success, false);
      assert.equal(response.json().error.code, code);
      assert.ok(!response.body.includes('private database details'));
    } finally { await app.close(); }
  }
});

test('missing or prose bands stay explicit without inferred levels or copied benchmark values', async () => {
  for (const [strategy, benchmark, availability, strategyLevel, benchmarkLevel] of [
    [null, null, 'UNKNOWN', null, null],
    ['Level 2', null, 'PARTIAL', 2, null],
    [null, 'Level 3', 'PARTIAL', null, 3],
    ['Very High', 'High', 'PUBLISHED', null, null],
    ['Risk Band Level 1', '5', 'PUBLISHED', 1, 5],
  ] as const) {
    const { app } = fixture([{ ...base, investmentStrategyRiskBand: strategy, benchmarkRiskBand: benchmark }]);
    try {
      const response = await app.inject(url(base.fullName));
      assert.equal(response.statusCode, 200);
      const { data } = response.json();
      assert.equal(data.availability, availability);
      assert.equal(data.investmentStrategyRiskBand, strategy);
      assert.equal(data.benchmarkRiskBand, benchmark);
      assert.equal(data.investmentStrategyRiskLevel, strategyLevel);
      assert.equal(data.benchmarkRiskLevel, benchmarkLevel);
    } finally { await app.close(); }
  }
});
