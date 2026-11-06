/** Compare the migrated database's read-only API with the original local API. */
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parse } from 'dotenv';
import { buildApp } from '../app.js';
import { readConfig } from '../config/env.js';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const { directory } = JSON.parse(readFileSync(resolve(root, 'data/supabase-migration-latest.json'), 'utf8'));
const roles = JSON.parse(readFileSync(resolve(directory, 'application-roles.json'), 'utf8'));
const target = parse(readFileSync(resolve(root, '.env.supabase')));
const config = readConfig({ ...process.env, ...target, LOG_LEVEL: 'silent', PGPOOL_MAX: '2',
  BACKEND_PGUSER: 'sif_backend.pduxixfhejkslwafxplw', BACKEND_PGPASSWORD: roles.sif_backend });
const app = await buildApp(config);
try {
  const paths = [
    '/health', '/api/liveTicker',
    '/api/analytics/category-comparison?to_date=2026-11-06',
    '/api/analytics/monthly-heatmap?range=all&to_date=2026-11-06',
    '/api/analytics/benchmarks?to_date=2026-11-06',
    '/api/analytics/performance?to_date=2026-11-06',
    '/api/analytics/benchmark-comparison?fund_id=1&benchmark_id=nifty50&to_date=2026-11-06',
    ...['altiva-hybrid-long-short-fund', 'apex-hybrid-long-short-fund',
      'qsif-hybrid-long-short-fund', 'magnum-hybrid-long-short-fund',
      'altiva-equity-long-short-fund'].map(slug => `/api/funds/${slug}`),
    '/api/funds/altiva-hybrid-long-short-fund/allocation',
    '/api/risk-band?scheme_name=Altiva%20Hybrid%20Long-Short%20Fund',
  ];
  const checks = [];
  for (const path of paths) {
    const started = Date.now();
    const local = await fetch(`http://127.0.0.1:3000${path}`);
    const source = await local.json();
    const cloud = await app.inject(path);
    assert.equal(cloud.statusCode, local.status, `HTTP status differs: ${path}`);
    assert.equal(cloud.statusCode, 200, `API failed: ${path}`);
    assert.deepEqual(cloud.json(), source, `Response differs: ${path}`);
    checks.push({ path, status: cloud.statusCode, exactMatch: true, elapsedMs: Date.now() - started });
    process.stdout.write(`Matched ${path}\n`);
  }
  writeFileSync(resolve(directory, 'api-verification.json'), JSON.stringify(checks, null, 2));
  process.stdout.write(`Verified ${checks.length} API responses using the read-only cloud role.\n`);
} finally { await app.close(); }
