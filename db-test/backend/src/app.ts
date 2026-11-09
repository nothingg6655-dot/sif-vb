import Fastify from 'fastify';
import { readConfig, type AppConfig } from './config/env.js';
import { createPool, verifyDatabase } from './config/database.js';
import { registerHealth } from './routes/health.js';
import { registerAdminSync } from './routes/admin-sync.js';
import { registerFundRoutes } from './routes/funds.js';
import { registerFundAllocationRoutes } from './routes/allocations.js';
import { registerRiskBandRoutes } from './routes/bands.js';
import { registerCategoryComparison } from './routes/category-comparison.js';
import { registerSchemePerformance } from './routes/scheme-performance.js';
import { registerPipelineMonitor } from './routes/pipeline-monitor.js';
import { InternalScheduler } from './services/internal-scheduler.js';
import { getServerMonitor } from './services/server-monitor.js';

export async function buildApp(config: AppConfig = readConfig()) {
  const app = Fastify({ logger: { level: config.logLevel, redact: ['req.headers.authorization'] } });
  const pool = createPool(config);
  let scheduler: InternalScheduler | undefined;
  pool.on('error', () => app.log.error('Idle database connection failed'));
  app.addHook('onClose', async () => { await scheduler?.close(); await pool.end(); });
  app.addHook('onRequest', async (request, reply) => {
    reply.header('Access-Control-Allow-Origin', '*');
    reply.header('Access-Control-Allow-Methods', 'GET, HEAD, POST, OPTIONS');
    reply.header('Access-Control-Allow-Headers', '*');
    if (request.method === 'OPTIONS') {
      return reply.status(204).send();
    }
  });
  try {
    await verifyDatabase(pool, config.database.database);
    registerHealth(app, pool, config.database.database);
    registerAdminSync(app, pool, config.syncFreshnessMaxMinutes, config.adminApiKey);
    registerFundRoutes(app, pool);
    registerFundAllocationRoutes(app, pool);
    registerRiskBandRoutes(app, pool);
    registerCategoryComparison(app, pool);
    registerSchemePerformance(app, pool, config.adminApiKey);
    if (config.scheduler.enabled) {
      scheduler = new InternalScheduler(config, app.log);
      await scheduler.initialize();
      app.addHook('onListen', async () => { scheduler!.start(); });
      registerPipelineMonitor(app, pool, config.syncFreshnessMaxMinutes,
        (database, freshness) => getServerMonitor(database, freshness, config.scheduler.scope));
    } else {
      registerPipelineMonitor(app, pool, config.syncFreshnessMaxMinutes);
    }
    return app;
  } catch (error) {
    await app.close();
    throw error;
  }
}
