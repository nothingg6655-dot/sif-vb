import { readConfig } from '../config/env.js';
import { createPool, verifyDatabase } from '../config/database.js';

try {
  const pool = createPool(readConfig());
  pool.on('error', () => console.error('Idle database connection failed'));
  try {
    await verifyDatabase(pool);
    console.log('Connected successfully: VB-SIF / public');
  } finally { await pool.end(); }
} catch {
  console.error('Database connection check failed. Check environment and database availability.');
  process.exitCode = 1;
}
