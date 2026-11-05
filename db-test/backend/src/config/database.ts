import pg from 'pg';
import type { AppConfig } from './env.js';

export function createPool(config: AppConfig) {
  return new pg.Pool(config.database);
}

export async function verifyDatabase(pool: pg.Pool): Promise<void> {
  const result = await pool.query<{ database: string; schema: string }>(
    'SELECT current_database() AS database, current_schema() AS schema'
  );
  if (result.rows[0]?.database !== 'VB-SIF' || result.rows[0]?.schema !== 'public') {
    throw new Error('Database target must be VB-SIF / public');
  }
}
