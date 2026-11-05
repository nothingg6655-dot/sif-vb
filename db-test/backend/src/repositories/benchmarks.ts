import type pg from 'pg';

export interface BenchmarkRecord {
  id: string;
  label: string;
  subtitle: string;
  indexVariant: 'PRICE' | 'TRI' | 'UNKNOWN';
  currency: string;
  source: string | null;
  sourceIdentifier: string | null;
  coverageStatus: 'available' | 'partial' | 'unavailable';
  metadata: Record<string, unknown>;
}

export interface BenchmarkHistoryRow { date: string; value: string }
export interface AllBenchmarkHistoryRow extends BenchmarkHistoryRow { benchmarkId: string }

export async function readBenchmarks(pool: pg.Pool): Promise<BenchmarkRecord[]> {
  return (await pool.query<BenchmarkRecord>(`
    SELECT benchmark_id AS id,label,subtitle,index_variant AS "indexVariant",currency,
           source,source_identifier AS "sourceIdentifier",
           coverage_status AS "coverageStatus",metadata
    FROM public.app_benchmarks ORDER BY benchmark_id
  `)).rows;
}

export async function readBenchmarkHistory(
  pool: pg.Pool, benchmarkId: string, toDate: string,
): Promise<BenchmarkHistoryRow[]> {
  return (await pool.query<BenchmarkHistoryRow>(`
    SELECT observation_date::text AS date,index_level::text AS value
    FROM public.app_benchmark_history
    WHERE benchmark_id=$1 AND observation_date <= $2::date
    ORDER BY observation_date
  `, [benchmarkId, toDate])).rows;
}

export async function readAllBenchmarkHistory(
  pool: pg.Pool, toDate: string,
): Promise<AllBenchmarkHistoryRow[]> {
  return (await pool.query<AllBenchmarkHistoryRow>(`
    SELECT benchmark_id AS "benchmarkId",observation_date::text AS date,
           index_level::text AS value
    FROM public.app_benchmark_history
    WHERE observation_date <= $1::date
    ORDER BY benchmark_id,observation_date
  `, [toDate])).rows;
}
