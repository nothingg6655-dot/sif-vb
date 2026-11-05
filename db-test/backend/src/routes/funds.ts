import type { FastifyInstance } from 'fastify';
import type pg from 'pg';
import {
  annualAnalytics, benchmarkCatalog, benchmarkComparison, benchmarkPerformance,
  fundDetails, heatmap, liveTicker, monthlyAnalytics, navHistory, performance,
  rollingAnalytics, sifTer, trendAnalytics, unifiedPerformance,
} from '../services/funds.js';
import { apiError, isIsoDate, positiveInteger } from '../utils/http.js';

function today(): string { return new Date().toISOString().slice(0, 10); }

export function registerFundRoutes(app: FastifyInstance, pool: pg.Pool) {
  app.get('/api/liveTicker', async (_request, reply) => {
    try {
      const result = await liveTicker(pool);
      const warnings = result.diagnostics.filter((item) => item.dataQualityWarnings.length > 0);
      if (warnings.length > 0) {
        reply.header('x-data-quality-warning-count', String(warnings.length));
        app.log.warn({ warnings }, 'Live ticker NAV data-quality warnings');
      }
      return { data: result.data };
    }
    catch { app.log.error('Live ticker query failed'); return apiError(reply, 500, 'TICKER_UNAVAILABLE', 'Live ticker is unavailable'); }
  });

  app.get<{ Querystring: Record<string, string | undefined> }>('/api/nav-history', async (request, reply) => {
    const { sd_id: sdId, from_date: fromDate, to_date: toDate } = request.query;
    const includePagination = request.query.page !== undefined || request.query.limit !== undefined;
    const page = positiveInteger(request.query.page, 1, 1000000);
    const limit = positiveInteger(request.query.limit, 1000, 1000);
    if (!sdId || !/^SIF-\d+$/.test(sdId) || !isIsoDate(fromDate) || !isIsoDate(toDate)
        || fromDate > toDate || page === null || limit === null) {
      return apiError(reply, 400, 'INVALID_QUERY',
        'sd_id, from_date, to_date, page, or limit is invalid');
    }
    try {
      const result = await navHistory(
        pool, sdId, fromDate, toDate, page, limit, includePagination
      );
      if (result === null) return apiError(reply, 404, 'SCHEME_NOT_FOUND', 'Scheme was not found');
      if (result === 'ambiguous') return apiError(reply, 409, 'AMBIGUOUS_SCHEME', 'Scheme identifier is ambiguous');
      return result;
    } catch {
      app.log.error('NAV history query failed');
      return apiError(reply, 500, 'NAV_HISTORY_UNAVAILABLE', 'NAV history is unavailable');
    }
  });

  app.get<{ Querystring: Record<string, string | undefined> }>('/api/heatmap-all', async (request, reply) => {
    const window = request.query.window;
    const toDate = request.query.to_date;
    if (!window || !['3m', '6m', '12m', 'all'].includes(window) || !isIsoDate(toDate)) {
      return apiError(reply, 400, 'INVALID_QUERY', 'window or to_date is invalid');
    }
    try { return await heatmap(pool, window as '3m'|'6m'|'12m'|'all', toDate); }
    catch { app.log.error('Heatmap query failed'); return apiError(reply, 500, 'HEATMAP_UNAVAILABLE', 'Heatmap is unavailable'); }
  });

  app.get<{ Querystring: Record<string, string | undefined> }>('/api/performance-all', async (request, reply) => {
    const toDate = request.query.to_date ?? today();
    const from1m = request.query.from1m;
    const from3m = request.query.from3m;
    if (!isIsoDate(toDate) || (from1m !== undefined && (!isIsoDate(from1m) || from1m > toDate))
        || (from3m !== undefined && (!isIsoDate(from3m) || from3m > toDate))) {
      return apiError(reply, 400, 'INVALID_QUERY', 'to_date, from1m, or from3m is invalid');
    }
    try { return await performance(pool, toDate, from1m, from3m); }
    catch { app.log.error('Performance query failed'); return apiError(reply, 500, 'PERFORMANCE_UNAVAILABLE', 'Performance data is unavailable'); }
  });

  app.get<{ Querystring: Record<string, string | undefined> }>(
    '/api/analytics/performance', async (request, reply) => {
      const toDate = request.query.to_date ?? today();
      if (!isIsoDate(toDate)) return apiError(reply, 400, 'INVALID_QUERY', 'to_date is invalid');
      try { return await unifiedPerformance(pool, toDate); }
      catch { app.log.error('Unified performance query failed'); return apiError(reply, 500, 'ANALYTICS_UNAVAILABLE', 'Performance analytics are unavailable'); }
    },
  );

  app.get<{ Querystring: Record<string, string | undefined> }>(
    '/api/analytics/monthly-heatmap', async (request, reply) => {
      const range = request.query.range;
      const toDate = request.query.to_date ?? today();
      if (!range || !['12m', '24m', '36m', '60m', 'all'].includes(range)
          || !isIsoDate(toDate)) {
        return apiError(reply, 400, 'INVALID_QUERY', 'range or to_date is invalid');
      }
      try {
        return await monthlyAnalytics(
          pool, range as '12m'|'24m'|'36m'|'60m'|'all', toDate,
        );
      } catch {
        app.log.error('Monthly analytics query failed');
        return apiError(reply, 500, 'ANALYTICS_UNAVAILABLE', 'Monthly analytics are unavailable');
      }
    },
  );

  app.get<{ Querystring: Record<string, string | undefined> }>(
    '/api/analytics/annual', async (request, reply) => {
      const toDate = request.query.to_date ?? today();
      if (!isIsoDate(toDate)) return apiError(reply, 400, 'INVALID_QUERY', 'to_date is invalid');
      try { return await annualAnalytics(pool, toDate); }
      catch { app.log.error('Annual analytics query failed'); return apiError(reply, 500, 'ANALYTICS_UNAVAILABLE', 'Annual analytics are unavailable'); }
    },
  );

  app.get<{ Querystring: Record<string, string | undefined> }>(
    '/api/analytics/rolling', async (request, reply) => {
      const fundId = positiveInteger(request.query.fund_id, 0, 1000);
      const window = request.query.window;
      const toDate = request.query.to_date ?? today();
      if (fundId === null || fundId === 0 || !window
          || !['1m', '3m', '6m', '1y'].includes(window) || !isIsoDate(toDate)) {
        return apiError(reply, 400, 'INVALID_QUERY', 'fund_id, window, or to_date is invalid');
      }
      try {
        const result = await rollingAnalytics(
          pool, fundId, window as '1m'|'3m'|'6m'|'1y', toDate,
        );
        return result ?? apiError(reply, 404, 'FUND_NOT_FOUND', 'Fund was not found');
      } catch {
        app.log.error('Rolling analytics query failed');
        return apiError(reply, 500, 'ANALYTICS_UNAVAILABLE', 'Rolling analytics are unavailable');
      }
    },
  );

  app.get<{ Querystring: Record<string, string | undefined> }>(
    '/api/analytics/trend', async (request, reply) => {
      const fundId = positiveInteger(request.query.fund_id, 0, 1000);
      const fromDate = request.query.from_date;
      const toDate = request.query.to_date ?? today();
      if (fundId === null || fundId === 0 || !isIsoDate(fromDate) || !isIsoDate(toDate)
          || fromDate > toDate) {
        return apiError(reply, 400, 'INVALID_QUERY', 'fund_id, from_date, or to_date is invalid');
      }
      try {
        const result = await trendAnalytics(pool, fundId, fromDate, toDate);
        return result ?? apiError(reply, 404, 'FUND_NOT_FOUND', 'Fund was not found');
      } catch {
        app.log.error('Trend analytics query failed');
        return apiError(reply, 500, 'ANALYTICS_UNAVAILABLE', 'Trend analytics are unavailable');
      }
    },
  );

  app.get<{ Querystring: Record<string, string | undefined> }>(
    '/api/analytics/benchmarks', async (request, reply) => {
      const toDate = request.query.to_date ?? today();
      if (!isIsoDate(toDate)) return apiError(reply, 400, 'INVALID_QUERY', 'to_date is invalid');
      try { return await benchmarkCatalog(pool, toDate); }
      catch { app.log.error('Benchmark analytics query failed'); return apiError(reply, 500, 'ANALYTICS_UNAVAILABLE', 'Benchmark analytics are unavailable'); }
    },
  );

  app.get<{ Querystring: Record<string, string | undefined> }>(
    '/api/analytics/benchmark-comparison', async (request, reply) => {
      const fundId = positiveInteger(request.query.fund_id, 0, 1000);
      const benchmarkId = request.query.benchmark_id;
      const toDate = request.query.to_date ?? today();
      if (fundId === null || fundId === 0 || !benchmarkId
          || !/^[a-z0-9]+$/.test(benchmarkId) || !isIsoDate(toDate)) {
        return apiError(reply, 400, 'INVALID_QUERY', 'fund_id, benchmark_id, or to_date is invalid');
      }
      try {
        const result = await benchmarkComparison(pool, fundId, benchmarkId, toDate);
        if (result === 'benchmark-not-found') {
          return apiError(reply, 404, 'BENCHMARK_NOT_FOUND', 'Benchmark was not found');
        }
        if (result === 'fund-not-found') {
          return apiError(reply, 404, 'FUND_NOT_FOUND', 'Fund was not found');
        }
        return result;
      } catch {
        app.log.error('Benchmark comparison query failed');
        return apiError(reply, 500, 'ANALYTICS_UNAVAILABLE', 'Benchmark comparison is unavailable');
      }
    },
  );

  app.get<{ Params: { slug: string } }>('/api/funds/:slug', async (request, reply) => {
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(request.params.slug)) {
      return apiError(reply, 400, 'INVALID_SLUG', 'Fund slug is invalid');
    }
    try {
      const result = await fundDetails(pool, request.params.slug);
      return result ?? apiError(reply, 404, 'FUND_NOT_FOUND', 'Fund was not found');
    } catch {
      app.log.error('Fund detail query failed');
      return apiError(reply, 500, 'FUND_UNAVAILABLE', 'Fund details are unavailable');
    }
  });

  app.get<{ Querystring: Record<string, string | undefined> }>(
    '/api/benchmark-performance', async (request, reply) => {
      const toDate = request.query.to_date ?? today();
      if (!isIsoDate(toDate)) return apiError(reply, 400, 'INVALID_QUERY', 'to_date is invalid');
      try { return await benchmarkPerformance(pool, toDate); }
      catch { app.log.error('Benchmark performance query failed'); return apiError(reply, 500, 'BENCHMARK_UNAVAILABLE', 'Benchmark performance is unavailable'); }
    },
  );
  app.get('/api/sif-ter', async () => sifTer());
}
