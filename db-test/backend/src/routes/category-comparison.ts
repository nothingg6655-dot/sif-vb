import type { FastifyInstance } from 'fastify';
import type pg from 'pg';
import { categoryComparison } from '../services/category-comparison.js';
import { apiError, isIsoDate } from '../utils/http.js';

export function registerCategoryComparison(app: FastifyInstance, pool: pg.Pool) {
  app.get<{ Querystring: Record<string, string | undefined> }>(
    '/api/analytics/category-comparison', async (request, reply) => {
      const toDate = request.query.to_date ?? new Date().toISOString().slice(0, 10);
      if (!isIsoDate(toDate)) return apiError(reply, 400, 'INVALID_QUERY', 'to_date is invalid');
      try { return await categoryComparison(pool, toDate); }
      catch {
        app.log.error('Category comparison query failed');
        return apiError(reply, 500, 'COMPARISON_UNAVAILABLE', 'Category comparison is unavailable');
      }
    },
  );
}
