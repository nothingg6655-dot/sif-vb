import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { createPool, verifyDatabase } from '../config/database.js';
import { readConfig } from '../config/env.js';
import { calculateAllSchemesPerformance } from '../services/performance.service.js';
import { serializeSchemePerformance } from '../services/scheme-performance-model.js';
import { isIsoDate } from './http.js';

const args = process.argv.slice(2);
const asOfDate = args.find((arg) => arg.startsWith('--as-of='))?.slice('--as-of='.length);
const output = args.find((arg) => arg.startsWith('--output='))?.slice('--output='.length);
if (asOfDate && !isIsoDate(asOfDate)) throw new Error('Invalid --as-of=YYYY-MM-DD');
const config = readConfig();
const pool = createPool(config);
try {
  await verifyDatabase(pool, config.database.database);
  const result = await calculateAllSchemesPerformance(pool, { asOfDate,
    onError: (failure) => console.error(JSON.stringify(failure)) });
  if (output) {
    const destination = resolve(output);
    mkdirSync(dirname(destination), { recursive: true });
    writeFileSync(destination, JSON.stringify({
      generatedAt: new Date().toISOString(), requestedAsOfDate: asOfDate ?? null,
      totalSchemes: result.totalSchemes, errors: result.errors,
      data: result.schemes.map((scheme) => serializeSchemePerformance(scheme)),
    }, null, 2));
  }
  console.log(JSON.stringify({ totalSchemes: result.totalSchemes, calculatedSchemes: result.schemes.length,
    unavailableSchemes: result.schemes.filter((scheme) => scheme.currentNav === null).length,
    returnRowsChanged: result.returnRowsChanged, riskRowsChanged: result.riskRowsChanged,
    errors: result.errors, requestedAsOfDate: asOfDate ?? null, output: output ?? null }));
  if (result.errors.length) process.exitCode = 1;
} finally { await pool.end(); }
