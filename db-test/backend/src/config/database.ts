import pg from 'pg';
import type { AppConfig } from './env.js';

export function createPool(config: AppConfig) {
  return new pg.Pool({ ...config.database,
    // Session poolers can ignore startup options, so initialize with SQL.
    onConnect: async (client: pg.ClientBase) => {
      await client.query("SELECT set_config('search_path','public',false), " +
        "set_config('TimeZone','Asia/Kolkata',false), set_config('statement_timeout',$1,false)",
      [String(config.database.statement_timeout)]);
    },
  });
}

export async function verifyDatabase(pool: pg.Pool, expectedDatabase = 'VB-SIF'): Promise<void> {
  const result = await pool.query<{ database: string; schema: string }>(
    'SELECT current_database() AS database, current_schema() AS schema'
  );
  if (result.rows[0]?.database !== expectedDatabase || result.rows[0]?.schema !== 'public') {
    throw new Error('Database target must match the configured database / public');
  }
}
