import type { FastifyInstance } from 'fastify';
import type pg from 'pg';
import {
  allocationDefinitions, allFundAllocations, fundAllocation, fundAllocationByName,
  normalizeSchemeName, type AllocationType,
} from '../services/allocations.js';
import { apiError } from '../utils/http.js';

const schemeNamePaths = {
  allocation: '/api/asset-allocation',
  'market-cap': '/api/market-cap-allocation',
  'sector-allocation': '/api/sector-allocation',
  'rating-allocation': '/api/rating-wise-allocation',
} as const;

export function registerFundAllocationRoutes(app: FastifyInstance, pool: pg.Pool) {
  for (const type of Object.keys(allocationDefinitions) as AllocationType[]) {
    app.get<{ Querystring: { scheme_name?: unknown } }>(schemeNamePaths[type], async (request, reply) => {
      const schemeName = request.query.scheme_name;
      if (typeof schemeName !== 'string' || !schemeName.trim()
          || schemeName.trim().length > 500 || !normalizeSchemeName(schemeName)) {
        return apiError(reply, 400, 'INVALID_QUERY', 'scheme_name must be a non-empty scheme name of at most 500 characters');
      }
      try {
        const result = await fundAllocationByName(pool, schemeName.trim(), type);
        if (result === null) return apiError(reply, 404, 'SCHEME_NOT_FOUND', 'Scheme name was not found in the strategy registry or verified scheme links');
        if (result === 'ambiguous') {
          return apiError(reply, 409, 'AMBIGUOUS_SCHEME', 'Scheme name matches multiple strategies');
        }
        if (result === 'factsheet-not-found') {
          return apiError(reply, 404, 'FACTSHEET_NOT_FOUND', 'No verified official factsheet is available');
        }
        return result;
      } catch {
        app.log.error({ allocationType: type }, 'Scheme-name allocation query failed');
        return apiError(reply, 500, 'ALLOCATION_UNAVAILABLE', 'Fund allocation data is unavailable');
      }
    });

    app.get(`/api/funds/allocations/${type}`, async (_request, reply) => {
      try {
        return await allFundAllocations(pool, type);
      } catch {
        app.log.error({ allocationType: type }, 'All-fund allocation query failed');
        return apiError(reply, 500, 'ALLOCATION_UNAVAILABLE', 'Fund allocation data is unavailable');
      }
    });

    app.get<{ Params: { slug: string } }>(`/api/funds/:slug/${type}`, async (request, reply) => {
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(request.params.slug)) {
        return apiError(reply, 400, 'INVALID_SLUG', 'Fund slug is invalid');
      }
      try {
        const result = await fundAllocation(pool, request.params.slug, type);
        if (result === null) return apiError(reply, 404, 'FUND_NOT_FOUND', 'Fund was not found');
        if (result === 'ambiguous') {
          return apiError(reply, 409, 'AMBIGUOUS_FUND', 'Fund slug matches multiple strategies');
        }
        if (result === 'factsheet-not-found') {
          return apiError(reply, 404, 'FACTSHEET_NOT_FOUND', 'No verified official factsheet is available');
        }
        return result;
      } catch {
        app.log.error({ slug: request.params.slug, allocationType: type }, 'Fund allocation query failed');
        return apiError(reply, 500, 'ALLOCATION_UNAVAILABLE', 'Fund allocation data is unavailable');
      }
    });
  }
}
