import { config as loadDotenv } from 'dotenv';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = fileURLToPath(new URL('../../../', import.meta.url));
const supabaseCa = fileURLToPath(new URL('../../certs/supabase-ca.crt', import.meta.url));
loadDotenv({ path: resolve(projectRoot, process.env.SIF_ENV_FILE ?? '.env'), quiet: true });

function required(env: NodeJS.ProcessEnv, name: string): string {
  const value = env[name];
  if (!value?.trim()) throw new Error(`Missing environment variable: ${name}`);
  return value;
}

function integer(env: NodeJS.ProcessEnv, name: string, fallback: number, max: number): number {
  const value = env[name] ?? String(fallback);
  if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > max) {
    throw new Error(`Invalid environment variable: ${name}`);
  }
  return Number(value);
}

export function readConfig(env: NodeJS.ProcessEnv = process.env) {
  const database = required(env, 'PGDATABASE');
  const expectedDatabase = env.PGEXPECTED_DATABASE?.trim() || 'VB-SIF';
  if (database !== expectedDatabase) throw new Error('PGDATABASE must match PGEXPECTED_DATABASE (default VB-SIF)');
  const sslmode = env.PGSSLMODE ?? 'disable';
  if (!['disable', 'verify-full'].includes(sslmode)) {
    throw new Error('PGSSLMODE must be disable or verify-full');
  }
  const production = env.NODE_ENV === 'production';
  if (production && sslmode !== 'verify-full') {
    throw new Error('Production requires PGSSLMODE=verify-full');
  }
  const databaseHost = required(env, 'PGHOST');
  const rootCertificate = env.PGSSLROOTCERT?.trim() ||
    (/\.(?:supabase\.com|supabase\.co)$/.test(databaseHost) ? supabaseCa : undefined);
  const timezone = env.APP_TIMEZONE ?? 'Asia/Kolkata';
  if (timezone !== 'Asia/Kolkata') throw new Error('APP_TIMEZONE must be Asia/Kolkata');
  const logLevel = env.LOG_LEVEL ?? 'info';
  if (!['fatal', 'error', 'warn', 'info', 'debug', 'trace', 'silent'].includes(logLevel)) {
    throw new Error('Invalid LOG_LEVEL');
  }
  const host = env.HOST ?? (production ? '0.0.0.0' : '127.0.0.1');
  const adminApiKey = env.ADMIN_API_KEY?.trim() || null;
  if (!['127.0.0.1', 'localhost', '::1'].includes(host) && adminApiKey === null) {
    throw new Error('ADMIN_API_KEY is required when HOST is not loopback');
  }
  return {
    host,
    port: integer(env, 'PORT', 3000, 65535),
    logLevel,
    syncFreshnessMaxMinutes: integer(env, 'SYNC_FRESHNESS_MAX_MINUTES', 1440, 525600),
    adminApiKey,
    database: {
      host: databaseHost,
      port: integer(env, 'PGPORT', 5432, 65535),
      database,
      user: env.BACKEND_PGUSER?.trim() || required(env, 'PGUSER'),
      password: env.BACKEND_PGPASSWORD || required(env, 'PGPASSWORD'),
      ssl: sslmode === 'verify-full' ? {
        rejectUnauthorized: true,
        ...(rootCertificate ? { ca: readFileSync(resolve(projectRoot, rootCertificate), 'utf8') } : {}),
      } : false,
      max: integer(env, 'PGPOOL_MAX', 5, 100),
      connectionTimeoutMillis: integer(env, 'PGCONNECT_TIMEOUT', 10, 300) * 1000,
      idleTimeoutMillis: integer(env, 'PGIDLE_TIMEOUT_MS', 30000, 3600000),
      statement_timeout: integer(env, 'PGSTATEMENT_TIMEOUT_MS', 15000, 3600000),
      application_name: 'vb-sif-api',
      options: '-c search_path=public -c timezone=Asia/Kolkata'
    }
  };
}

export type AppConfig = ReturnType<typeof readConfig>;
