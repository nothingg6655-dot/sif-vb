import type { FastifyInstance } from 'fastify';
import type pg from 'pg';
import { verifyDatabase } from '../config/database.js';

export function registerHealth(app: FastifyInstance, pool: pg.Pool) {
  app.get('/health', {
    schema: {
      response: {
        200: {
          type: 'object', additionalProperties: false,
          required: ['status', 'database'],
          properties: { status: { const: 'ok' }, database: { const: 'connected' } }
        },
        503: {
          type: 'object', additionalProperties: false,
          required: ['status', 'database'],
          properties: { status: { const: 'unavailable' }, database: { const: 'unavailable' } }
        }
      }
    }
  }, async (_request, reply) => {
    try {
      await verifyDatabase(pool);
      return { status: 'ok', database: 'connected' };
    } catch {
      app.log.warn('Database health check failed');
      return reply.code(503).send({ status: 'unavailable', database: 'unavailable' });
    }
  });
}
