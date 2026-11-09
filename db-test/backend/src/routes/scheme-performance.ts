import { timingSafeEqual } from 'node:crypto';
import type { FastifyInstance, FastifyRequest } from 'fastify';
import type pg from 'pg';
import { z } from 'zod';
import { isIsoDate, apiError } from '../utils/http.js';
import { calculateAllSchemesPerformance, SchemeSelectionError } from '../services/performance.service.js';
import { readCalculatedSchemePerformance } from '../repositories/scheme-performance.js';
import { performancePeriods, serializeSchemePerformance } from '../services/scheme-performance-model.js';

const date = z.string().refine(isIsoDate, 'Expected a valid YYYY-MM-DD date');
const bodySchema = z.object({ asOfDate: date.optional() }).strict();
const querySchema = z.object({ asOfDate: date.optional(), period: z.enum(performancePeriods).optional() }).strict();
const schemeSchema = z.string().regex(/^[A-Za-z0-9_-]+(?::\d+)?$/).max(100);

export function registerSchemePerformance(app: FastifyInstance, pool: pg.Pool, adminApiKey: string | null) {
  const mayCalculate = (request: FastifyRequest) => {
    if (adminApiKey === null) return true;
    const header = request.headers.authorization;
    if (!header?.startsWith('Bearer ')) return false;
    const expected = Buffer.from(adminApiKey), supplied = Buffer.from(header.slice(7));
    return expected.length === supplied.length && timingSafeEqual(expected, supplied);
  };
  for (const individual of [false, true]) {
    const path = individual ? '/api/performance/:schemeId/calculate' : '/api/performance/calculate-all';
    app.post<{ Params: { schemeId?: string } }>(path, async (request, reply) => {
      if (!mayCalculate(request)) return apiError(reply, 401, 'UNAUTHORIZED', 'Administrative authentication is required');
      const body = bodySchema.safeParse(request.body ?? {});
      if (!body.success || (individual && !schemeSchema.safeParse(request.params.schemeId).success)) {
        return apiError(reply, 400, 'INVALID_REQUEST', 'schemeId or asOfDate is invalid');
      }
      try {
        const result = await calculateAllSchemesPerformance(pool, {
          asOfDate: body.data.asOfDate, schemeId: individual ? request.params.schemeId : undefined,
          onError: (error) => app.log.warn(error, 'Scheme calculation failed'),
        });
        if (individual && result.errors.length) return apiError(reply, 422, 'INVALID_NAV_SERIES', result.errors[0]!.message);
        const data = result.schemes.map((scheme) => serializeSchemePerformance(scheme));
        return { success: result.errors.length === 0, totalSchemes: result.totalSchemes,
          calculatedSchemes: data.length, returnRowsChanged: result.returnRowsChanged,
          riskRowsChanged: result.riskRowsChanged, errors: result.errors, data: individual ? data[0] : data };
      } catch (error) {
        if (error instanceof SchemeSelectionError) return apiError(reply,
          error.code === 'SCHEME_NOT_FOUND' ? 404 : 409, error.code, 'Scheme identifier was not found or is ambiguous');
        app.log.error('Scheme performance calculation failed');
        return apiError(reply, 500, 'CALCULATION_UNAVAILABLE', 'Scheme performance could not be calculated');
      }
    });
  }
  for (const individual of [false, true]) {
    app.get<{ Params: { schemeId?: string }; Querystring: Record<string, string | undefined> }>(
      individual ? '/api/performance/:schemeId' : '/api/performance', async (request, reply) => {
        const query = querySchema.safeParse(request.query);
        if (!query.success || (individual && !schemeSchema.safeParse(request.params.schemeId).success)) {
          return apiError(reply, 400, 'INVALID_QUERY', 'schemeId, asOfDate, or period is invalid');
        }
        try {
          const schemes = await readCalculatedSchemePerformance(pool, {
            ...query.data, schemeId: individual ? request.params.schemeId : undefined,
          });
          if (individual && schemes.length !== 1) return apiError(reply, schemes.length ? 409 : 404,
            schemes.length ? 'AMBIGUOUS_SCHEME' : 'METRICS_NOT_FOUND', 'Calculated scheme metrics were not found or are ambiguous');
          const data = schemes.map((scheme) => serializeSchemePerformance(scheme, query.data.period));
          return { success: true, count: data.length, data: individual ? data[0] : data };
        } catch {
          app.log.error('Scheme performance retrieval failed');
          return apiError(reply, 500, 'METRICS_UNAVAILABLE', 'Calculated performance is unavailable');
        }
      },
    );
  }
}
