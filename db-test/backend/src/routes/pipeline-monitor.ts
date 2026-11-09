import type { FastifyInstance } from 'fastify';
import type pg from 'pg';
import { getPipelineMonitor } from '../services/pipeline-monitor.js';
import { apiError } from '../utils/http.js';

export function registerPipelineMonitor(app: FastifyInstance, pool: pg.Pool, freshnessMaxMinutes: number,
  monitor = getPipelineMonitor) {
  // Read-only, sanitized monitoring; no admin credential is embedded in Flutter.
  app.get('/api/sync/status', async (_request, reply) => {
    try { return { success: true, data: await monitor(pool, freshnessMaxMinutes) }; }
    catch {
      app.log.warn('Pipeline monitor check failed');
      return apiError(reply, 503, 'SYNC_CHECK_UNAVAILABLE', 'Sync checks are unavailable.');
    }
  });
}
