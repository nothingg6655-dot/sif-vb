import { config as loadDotenv } from 'dotenv';
import { fileURLToPath } from 'node:url';

loadDotenv({ path: fileURLToPath(new URL('../../../.env', import.meta.url)), quiet: true });

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
  if (database !== 'VB-SIF') throw new Error('PGDATABASE must be VB-SIF');
  const sslmode = env.PGSSLMODE ?? 'disable';
  if (!['disable', 'verify-full'].includes(sslmode)) {
    throw new Error('PGSSLMODE must be disable or verify-full');
  }
  const timezone = env.APP_TIMEZONE ?? 'Asia/Kolkata';
  if (timezone !== 'Asia/Kolkata') throw new Error('APP_TIMEZONE must be Asia/Kolkata');
  const logLevel = env.LOG_LEVEL ?? 'info';
  if (!['fatal', 'error', 'warn', 'info', 'debug', 'trace', 'silent'].includes(logLevel)) {
    throw new Error('Invalid LOG_LEVEL');
  }
  const host = env.HOST ?? '127.0.0.1';
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
      host: required(env, 'PGHOST'),
      port: integer(env, 'PGPORT', 5432, 65535),
      database,
      user: required(env, 'PGUSER'),
      password: required(env, 'PGPASSWORD'),
      ssl: sslmode === 'verify-full' ? { rejectUnauthorized: true } : false,
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
