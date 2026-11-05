import type { FastifyInstance } from 'fastify';
import type pg from 'pg';
import { timingSafeEqual } from 'node:crypto';
import { getSyncStatus } from '../services/sync-status.js';
import { apiError } from '../utils/http.js';

function authorized(header: string | undefined, apiKey: string): boolean {
  if (!header?.startsWith('Bearer ')) return false;
  const supplied = Buffer.from(header.slice(7));
  const expected = Buffer.from(apiKey);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

export function registerAdminSync(
  app: FastifyInstance, pool: pg.Pool, freshnessMaxMinutes: number, adminApiKey: string | null,
) {
  app.get('/api/admin/tigzig/sync/status', {
    preHandler: async (request, reply) => {
      if (adminApiKey !== null && !authorized(request.headers.authorization, adminApiKey)) {
        return apiError(reply, 401, 'UNAUTHORIZED', 'Administrative authentication is required');
      }
    }
  }, async (_request, reply) => {
    try {
      return { success: true, data: await getSyncStatus(pool, freshnessMaxMinutes) };
    } catch {
      app.log.warn('TIGZIG sync status query failed');
      return reply.code(503).send({
        success: false,
        error: { code: 'SYNC_STATUS_UNAVAILABLE', message: 'Sync status is unavailable' }
      });
    }
  });
}
