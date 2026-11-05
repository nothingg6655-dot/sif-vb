import type { FastifyInstance } from 'fastify';
import type pg from 'pg';
import { riskBandByName } from '../services/bands.js';
import { normalizeSchemeName } from '../services/allocations.js';
import { apiError } from '../utils/http.js';

export function registerRiskBandRoutes(app: FastifyInstance, pool: pg.Pool) {
  for (const path of ['/api/band', '/api/risk-band']) {
    app.get<{ Querystring: { scheme_name?: unknown } }>(path, async (request, reply) => {
      const name = request.query.scheme_name;
      if (typeof name !== 'string' || !name.trim() || name.trim().length > 500
          || !normalizeSchemeName(name)) {
        return apiError(reply, 400, 'INVALID_QUERY', 'scheme_name must be a non-empty scheme name of at most 500 characters');
      }
      try {
        const result = await riskBandByName(pool, name.trim());
        if (result === null) return apiError(reply, 404, 'SCHEME_NOT_FOUND', 'Scheme name was not found in the strategy registry or verified scheme links');
        if (result === 'ambiguous') {
          return apiError(reply, 409, 'AMBIGUOUS_SCHEME', 'Scheme name matches multiple strategies');
        }
        if (result === 'factsheet-not-found') {
          return apiError(reply, 404, 'FACTSHEET_NOT_FOUND', 'No verified official factsheet is available');
        }
        return result;
      } catch {
        app.log.error('Risk band query failed');
        return apiError(reply, 500, 'BAND_UNAVAILABLE', 'Risk band data is unavailable');
      }
    });
  }
}
