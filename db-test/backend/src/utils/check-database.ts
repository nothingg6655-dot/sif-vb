import { readConfig } from '../config/env.js';
import { createPool, verifyDatabase } from '../config/database.js';

try {
  const config = readConfig();
  const pool = createPool(config);
  pool.on('error', () => console.error('Idle database connection failed'));
  try {
    await verifyDatabase(pool, config.database.database);
    console.log(`Connected successfully: ${config.database.database} / public`);
  } finally { await pool.end(); }
} catch {
  console.error('Database connection check failed. Check environment and database availability.');
  process.exitCode = 1;
}
