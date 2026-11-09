import type pg from 'pg';
import {
  PERFORMANCE_VERSION, FinancialDecimal, performancePeriods,
  type SchemeSeries, type SchemePerformance, type SchemeInfo, type PerformancePeriod,
} from '../services/scheme-performance-model.js';

interface SchemeNavRow extends Omit<SchemeInfo, 'schemeId'> { date: string | null; nav: string | null }

export async function readSchemeNavSeries(pool: pg.Pool, asOfDate?: string, schemeId?: string): Promise<SchemeSeries[]> {
  const result = await pool.query<SchemeNavRow>(`
    SELECT s.source,s.scheme_code::text AS "schemeCode",s.temp_sif_code AS "externalSchemeId",
           s.scheme_name AS "schemeName",s.scheme_plan AS plan,s.scheme_option AS option,
           a.original_amc_name AS amc,n.nav_date::text AS date,n.nav::text
    FROM public.tigzig_schemes s
    JOIN public.tigzig_fund_houses a USING(fund_house_id)
    LEFT JOIN public.tigzig_scheme_nav n ON (n.source,n.scheme_code)=(s.source,s.scheme_code)
      AND ($1::date IS NULL OR n.nav_date <= $1)
    WHERE $2::text IS NULL OR s.source || ':' || s.scheme_code::text = $2
      OR s.temp_sif_code = $2 OR s.scheme_code::text = $2
    ORDER BY s.source,s.scheme_code,n.nav_date`, [asOfDate ?? null, schemeId ?? null]);
  const schemes = new Map<string, SchemeSeries>();
  for (const { date, nav, ...scheme } of result.rows) {
    const id = `${scheme.source}:${scheme.schemeCode}`;
    let series = schemes.get(id);
    if (!series) { series = { scheme: { ...scheme, schemeId: id }, observations: [] }; schemes.set(id, series); }
    if (date !== null) series.observations.push({ date, nav });
  }
  return [...schemes.values()];
}

export function metricRecords(schemes: SchemePerformance[]) {
  return schemes.flatMap((scheme) => scheme.asOfDate === null ? [] : performancePeriods.map((period) => {
    const metric = scheme.periods[period];
    return {
      source: scheme.scheme.source, schemeCode: scheme.scheme.schemeCode, asOfDate: scheme.asOfDate,
      horizon: period, version: PERFORMANCE_VERSION, targetDate: metric.targetDate,
      startDate: metric.startingNavDate, endDate: scheme.currentNavDate,
      startNav: metric.startingNav?.toString() ?? null, endNav: scheme.currentNav?.toString() ?? null,
      // Persist final display values directly to avoid double rounding (for
      // example a value just below 0.005 must not become 0.01 after a cache read).
      returnPct: metric.absoluteReturn?.toDecimalPlaces(2).toFixed(2) ?? null,
      volatilityPct: metric.annualizedVolatility?.toDecimalPlaces(2).toFixed(2) ?? null,
      observationCount: metric.observationCount,
      lookbackDays: metric.targetDate === null ? null
        : Math.round((Date.parse(scheme.asOfDate!) - Date.parse(metric.targetDate)) / 86400000),
      metadata: {
        sourceFingerprint: scheme.sourceFingerprint, currentNavDate: scheme.currentNavDate,
        inceptionDate: scheme.inceptionDate, firstReturnDate: metric.firstReturnDate,
        previousNavDate: metric.previousNavDate, status: metric.status, warnings: scheme.warnings,
        absoluteReturnExact: metric.absoluteReturn?.toString() ?? null,
        annualizedVolatilityExact: metric.annualizedVolatility?.toString() ?? null,
      },
    };
  }));
}

export async function persistSchemePerformance(pool: pg.Pool, schemes: SchemePerformance[]) {
  const records = metricRecords(schemes);
  if (!records.length) return { returnRowsChanged: 0, riskRowsChanged: 0 };
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query("SET LOCAL lock_timeout = '5s'");
    await client.query("SELECT pg_advisory_xact_lock(hashtext('sif-scheme-performance-v1'))");
    const returns = await client.query(`
      INSERT INTO public.sif_scheme_return_cache AS existing (
        source,scheme_code,as_of_date,horizon,method,calculation_version,target_date,
        start_nav_date,end_nav_date,start_nav,end_nav,return_pct,observation_count,calculation_metadata
      ) SELECT source,"schemeCode","asOfDate",horizon,'ABSOLUTE',version,"targetDate",
               "startDate","endDate","startNav","endNav","returnPct","observationCount",metadata
      FROM jsonb_to_recordset($1::jsonb) AS x(
        source text,"schemeCode" bigint,"asOfDate" date,horizon text,version text,"targetDate" date,
        "startDate" date,"endDate" date,"startNav" numeric,"endNav" numeric,"returnPct" numeric,
        "observationCount" integer,metadata jsonb)
      ON CONFLICT (source,scheme_code,as_of_date,horizon,method,calculation_version) DO UPDATE SET
        target_date=EXCLUDED.target_date,start_nav_date=EXCLUDED.start_nav_date,
        end_nav_date=EXCLUDED.end_nav_date,start_nav=EXCLUDED.start_nav,end_nav=EXCLUDED.end_nav,
        return_pct=EXCLUDED.return_pct,observation_count=EXCLUDED.observation_count,
        calculation_metadata=EXCLUDED.calculation_metadata,computed_at=now(),updated_at=now()
      WHERE ROW(existing.target_date,existing.start_nav_date,existing.end_nav_date,existing.start_nav,
                existing.end_nav,existing.return_pct,existing.observation_count,existing.calculation_metadata)
        IS DISTINCT FROM ROW(EXCLUDED.target_date,EXCLUDED.start_nav_date,EXCLUDED.end_nav_date,
          EXCLUDED.start_nav,EXCLUDED.end_nav,EXCLUDED.return_pct,EXCLUDED.observation_count,EXCLUDED.calculation_metadata)
    `, [JSON.stringify(records)]);
    const risk = await client.query(`
      INSERT INTO public.sif_scheme_risk_cache AS existing (
        source,scheme_code,as_of_date,horizon,lookback_days,calculation_version,
        annualized_volatility_pct,observation_count
      ) SELECT source,"schemeCode","asOfDate",horizon,"lookbackDays",version,"volatilityPct","observationCount"
      FROM jsonb_to_recordset($1::jsonb) AS x(
        source text,"schemeCode" bigint,"asOfDate" date,horizon text,"lookbackDays" integer,
        version text,"volatilityPct" numeric,"observationCount" integer)
      ON CONFLICT (source,scheme_code,as_of_date,horizon,calculation_version) DO UPDATE SET
        lookback_days=EXCLUDED.lookback_days,annualized_volatility_pct=EXCLUDED.annualized_volatility_pct,
        observation_count=EXCLUDED.observation_count,computed_at=now(),updated_at=now()
      WHERE ROW(existing.lookback_days,existing.annualized_volatility_pct,existing.observation_count)
        IS DISTINCT FROM ROW(EXCLUDED.lookback_days,EXCLUDED.annualized_volatility_pct,EXCLUDED.observation_count)
    `, [JSON.stringify(records)]);
    await client.query('COMMIT');
    return { returnRowsChanged: returns.rowCount ?? 0, riskRowsChanged: risk.rowCount ?? 0 };
  } catch (error) { await client.query('ROLLBACK'); throw error; }
  finally { client.release(); }
}

export async function readCalculatedSchemePerformance(pool: pg.Pool, options: {
  schemeId?: string; asOfDate?: string; period?: PerformancePeriod;
}): Promise<SchemePerformance[]> {
  const rows = (await pool.query<{
    source: string; schemeCode: string; externalSchemeId: string | null; schemeName: string;
    plan: string | null; option: string | null; amc: string | null; asOfDate: string;
    horizon: PerformancePeriod; startDate: string | null; endDate: string | null;
    startNav: string | null; endNav: string | null; targetDate: string | null;
    returnPct: string | null; volatilityPct: string | null; observationCount: number;
    metadata: { sourceFingerprint: string; inceptionDate: string | null; firstReturnDate: string | null;
      previousNavDate: string | null; status: 'complete' | 'insufficient-history'; warnings: string[];
      absoluteReturnExact?: string | null; annualizedVolatilityExact?: string | null };
  }>(`
    WITH dates AS (
      SELECT source,scheme_code,max(as_of_date) AS as_of_date
      FROM public.sif_scheme_return_cache
      WHERE calculation_version=$1 AND method='ABSOLUTE' AND ($2::date IS NULL OR as_of_date=$2)
      GROUP BY source,scheme_code
    )
    SELECT s.source,s.scheme_code::text AS "schemeCode",s.temp_sif_code AS "externalSchemeId",
           s.scheme_name AS "schemeName",s.scheme_plan AS plan,s.scheme_option AS option,
           a.original_amc_name AS amc,r.as_of_date::text AS "asOfDate",r.horizon,
           r.start_nav_date::text AS "startDate",r.end_nav_date::text AS "endDate",
           r.start_nav::text AS "startNav",r.end_nav::text AS "endNav",r.target_date::text AS "targetDate",
           r.return_pct::text AS "returnPct",v.annualized_volatility_pct::text AS "volatilityPct",
           v.observation_count AS "observationCount",r.calculation_metadata AS metadata
    FROM dates d JOIN public.sif_scheme_return_cache r USING(source,scheme_code,as_of_date)
    JOIN public.sif_scheme_risk_cache v ON
      (v.source,v.scheme_code,v.as_of_date,v.horizon,v.calculation_version)=
      (r.source,r.scheme_code,r.as_of_date,r.horizon,r.calculation_version)
    JOIN public.tigzig_schemes s ON (s.source,s.scheme_code)=(r.source,r.scheme_code)
    JOIN public.tigzig_fund_houses a ON a.fund_house_id=s.fund_house_id
    WHERE r.calculation_version=$1 AND r.method='ABSOLUTE'
      AND ($3::text IS NULL OR s.source || ':' || s.scheme_code::text=$3 OR s.temp_sif_code=$3 OR s.scheme_code::text=$3)
      AND ($4::text IS NULL OR r.horizon=$4)
    ORDER BY s.source,s.scheme_code,r.horizon`, [
    PERFORMANCE_VERSION, options.asOfDate ?? null, options.schemeId ?? null, options.period ?? null,
  ])).rows;
  const result = new Map<string, SchemePerformance>();
  const decimal = (value: string | null) => value === null ? null : new FinancialDecimal(value);
  for (const row of rows) {
    const id = `${row.source}:${row.schemeCode}`;
    let fund = result.get(id);
    if (!fund) {
      fund = {
        scheme: { schemeId: id, source: row.source, schemeCode: row.schemeCode, externalSchemeId: row.externalSchemeId,
          schemeName: row.schemeName, plan: row.plan, option: row.option, amc: row.amc },
        asOfDate: row.asOfDate, currentNav: decimal(row.endNav), currentNavDate: row.endDate,
        inceptionDate: row.metadata.inceptionDate, calculationVersion: PERFORMANCE_VERSION,
        sourceFingerprint: row.metadata.sourceFingerprint, warnings: row.metadata.warnings,
        periods: {} as SchemePerformance['periods'],
      };
      result.set(id, fund);
    }
    fund.periods[row.horizon] = {
      absoluteReturn: decimal(row.metadata.absoluteReturnExact ?? row.returnPct),
      annualizedVolatility: decimal(row.metadata.annualizedVolatilityExact ?? row.volatilityPct),
      startingNav: decimal(row.startNav), startingNavDate: row.startDate, targetDate: row.targetDate,
      observationCount: row.observationCount, firstReturnDate: row.metadata.firstReturnDate,
      previousNavDate: row.metadata.previousNavDate, status: row.metadata.status,
    };
  }
  return [...result.values()];
}
