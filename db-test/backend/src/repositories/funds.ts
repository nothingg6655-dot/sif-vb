import type pg from 'pg';
import type { DailyNavRow } from '../services/daily-change.js';

export interface PreferredNavRow {
  displayOrder: number;
  fundId: number;
  fullName: string;
  slug: string;
  source: string;
  schemeCode: number;
  date: string;
  nav: string;
}

export async function readLiveTicker(pool: pg.Pool) {
  return (await pool.query<DailyNavRow>(`
    WITH ranked AS (
      SELECT c.reference_fund_id AS "displayOrder",f.fund_id::integer AS "fundId",
             f.canonical_name AS "fullName",c.preferred_source AS source,
             c.preferred_scheme_code::integer AS "schemeCode",
             s.scheme_plan AS plan,s.scheme_option AS option,
             n.nav_date::text AS date,n.nav::text,
             row_number() OVER (
               PARTITION BY c.preferred_source,c.preferred_scheme_code
               ORDER BY n.nav_date DESC
             ) AS observation_rank
      FROM public.app_performance_config c
      JOIN public.app_funds f USING(fund_id)
      JOIN public.tigzig_schemes s
        ON (s.source,s.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
      JOIN public.tigzig_scheme_nav n
        ON (n.source,n.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
      WHERE c.selection_verified AND c.mapping_confidence IN ('high','medium')
        AND f.status='active' AND s.scheme_plan='Regular' AND s.scheme_option='Growth'
    )
    SELECT "displayOrder","fundId","fullName",source,"schemeCode",plan,option,date,nav
    FROM ranked WHERE observation_rank <= 2
    ORDER BY "displayOrder",observation_rank`)).rows;
}

export async function readPreferredNav(pool: pg.Pool, toDate: string): Promise<PreferredNavRow[]> {
  return (await pool.query<PreferredNavRow>(`
    SELECT t.display_order AS "displayOrder",f.fund_id::integer AS "fundId",
           f.canonical_name AS "fullName",f.slug,t.preferred_source AS source,
           t.preferred_scheme_code::integer AS "schemeCode",
           n.nav_date::text AS date,n.nav::text
    FROM public.app_ticker_config t
    JOIN public.app_funds f USING(fund_id)
    JOIN public.tigzig_scheme_nav n
      ON (n.source,n.scheme_code)=(t.preferred_source,t.preferred_scheme_code)
    WHERE t.is_visible AND t.selection_verified AND f.status='active'
      AND n.nav_date <= $1::date
    ORDER BY t.display_order,n.nav_date`, [toDate])).rows;
}

export async function readPerformanceNav(
  pool: pg.Pool, toDate: string, referenceFundId?: number,
): Promise<PreferredNavRow[]> {
  return (await pool.query<PreferredNavRow>(`
    SELECT c.reference_fund_id AS "displayOrder",f.fund_id::integer AS "fundId",
           f.canonical_name AS "fullName",f.slug,c.preferred_source AS source,
           c.preferred_scheme_code::integer AS "schemeCode",
           n.nav_date::text AS date,n.nav::text
    FROM public.app_performance_config c
    JOIN public.app_funds f USING(fund_id)
    JOIN public.tigzig_schemes s
      ON (s.source,s.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
    JOIN public.tigzig_scheme_nav n
      ON (n.source,n.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
    WHERE c.selection_verified AND c.mapping_confidence IN ('high','medium')
      AND f.status='active' AND s.scheme_plan='Regular' AND s.scheme_option='Growth'
      AND n.nav_date <= $1::date
      AND ($2::integer IS NULL OR c.reference_fund_id=$2)
    ORDER BY c.reference_fund_id,n.nav_date`, [toDate, referenceFundId ?? null])).rows;
}

export async function resolveScheme(pool: pg.Pool, sdId: string) {
  return (await pool.query<{ source: string; schemeCode: number }>(`
    SELECT source,scheme_code::integer AS "schemeCode"
    FROM public.tigzig_schemes WHERE temp_sif_code=$1 AND scheme_plan='Regular' ORDER BY source
  `, [sdId])).rows;
}

export async function readNavHistory(
  pool: pg.Pool, source: string, schemeCode: number,
  fromDate: string, toDate: string, limit: number, offset: number,
) {
  const [rows, count] = await Promise.all([
    pool.query<{ date: string; nav: string }>(`
      SELECT nav_date::text AS date,nav::text FROM public.tigzig_scheme_nav
      WHERE source=$1 AND scheme_code=$2 AND nav_date BETWEEN $3::date AND $4::date
      ORDER BY nav_date LIMIT $5 OFFSET $6
    `, [source, schemeCode, fromDate, toDate, limit, offset]),
    pool.query<{ total: number }>(`
      SELECT count(*)::integer AS total FROM public.tigzig_scheme_nav
      WHERE source=$1 AND scheme_code=$2 AND nav_date BETWEEN $3::date AND $4::date
    `, [source, schemeCode, fromDate, toDate])
  ]);
  return { rows: rows.rows, total: count.rows[0]?.total ?? 0 };
}

export async function readFundDetails(pool: pg.Pool, slug: string) {
  const fund = (await pool.query<{
    fundId: number; fullName: string; slug: string; status: string; createdAt: Date; updatedAt: Date;
  }>(`
    SELECT fund_id::integer AS "fundId",canonical_name AS "fullName",slug,status,
           created_at AS "createdAt",updated_at AS "updatedAt"
    FROM public.app_funds WHERE slug=$1
  `, [slug])).rows[0];
  if (!fund) return null;
  const [schemes, aaum, source, profile] = await Promise.all([
    pool.query<{
      source: string; schemeCode: number; tempSifCode: string | null; schemeName: string;
      schemeType: string | null; category: string | null; categorySub: string | null;
      plan: string | null; option: string | null; isActive: boolean | null;
      latestNavDate: string | null; latestNav: string | null; dailyChangePct: string | null;
      isins: Array<{ type: string; value: string }>;
    }>(`
      SELECT s.source,s.scheme_code::integer AS "schemeCode",s.temp_sif_code AS "tempSifCode",
             s.scheme_name AS "schemeName",s.scheme_type AS "schemeType",s.category,
             s.category_sub AS "categorySub",s.scheme_plan AS plan,s.scheme_option AS option,
             s.is_active AS "isActive",p.as_of_date::text AS "latestNavDate",
             p.latest_nav::text AS "latestNav",p.daily_return_pct::text AS "dailyChangePct",
             COALESCE(jsonb_agg(jsonb_build_object('type',i.identifier_type,'value',i.isin)
               ORDER BY i.identifier_type) FILTER (WHERE i.isin IS NOT NULL),'[]'::jsonb) AS isins
      FROM public.app_tigzig_fund_mapping m
      JOIN public.tigzig_schemes s USING(source,scheme_code)
      LEFT JOIN public.tigzig_scheme_isins i USING(source,scheme_code)
      LEFT JOIN public.app_performance_cache p USING(source,scheme_code)
      WHERE m.fund_id=$1 AND s.scheme_plan='Regular'
      GROUP BY s.source,s.scheme_code,p.as_of_date,p.latest_nav,p.daily_return_pct
      ORDER BY CASE s.scheme_option WHEN 'Growth' THEN 0 ELSE 1 END,s.scheme_code
    `, [fund.fundId]),
    pool.query<{
      source: string; schemeCode: number; schemeName: string; quarter: string;
      quarterStart: string; quarterEnd: string; aaumLakhs: string; aaumCr: string;
    }>(`
      SELECT a.source,a.scheme_code::integer AS "schemeCode",s.scheme_name AS "schemeName",
             a.aaum_quarter AS quarter,a.quarter_start::text AS "quarterStart",
             a.quarter_end::text AS "quarterEnd",a.aaum_lakhs::text AS "aaumLakhs",
             a.aaum_cr::text AS "aaumCr"
      FROM public.app_tigzig_fund_mapping m
      JOIN public.tigzig_scheme_aaum a USING(source,scheme_code)
      JOIN public.tigzig_schemes s USING(source,scheme_code)
      WHERE m.fund_id=$1 AND s.scheme_plan='Regular' ORDER BY a.quarter_end DESC,a.scheme_code
    `, [fund.fundId]),
    pool.query<{
      manifestGeneratedAt: Date | null; lastSuccessfulImportAt: Date | null;
      latestNavPublicationRaw: string | null; latestNavPublicationIst: Date | null;
    }>(`
      SELECT (SELECT max(manifest_generated_at) FROM public.tigzig_sync_file_versions)
               AS "manifestGeneratedAt",
             (SELECT max(last_successful_import_at) FROM public.tigzig_sync_file_versions)
               AS "lastSuccessfulImportAt",
             n.upload_ts_raw AS "latestNavPublicationRaw",
             n.upload_ts_ist AS "latestNavPublicationIst"
      FROM public.app_performance_config t
      LEFT JOIN LATERAL (
        SELECT upload_ts_raw,upload_ts_ist FROM public.tigzig_scheme_nav
        WHERE (source,scheme_code)=(t.preferred_source,t.preferred_scheme_code)
        ORDER BY nav_date DESC LIMIT 1
      ) n ON true WHERE t.fund_id=$1
    `, [fund.fundId]),
    pool.query<{
      reportId: number; reportAsOf: string; pdfUrl: string | null;
      benchmarkName: string | null; expenseRatioPercent: string | null;
      expenseRatioType: string | null; expenseRatioAsOf: string | null;
      netAumCr: string | null; aumAsOf: string | null; launchDate: string | null;
      managers: Array<{ name: string; role: string; managingSince: string | null }>;
    }>(`
      SELECT r.report_id::integer AS "reportId",
             coalesce(r.portfolio_as_of,r.report_month)::text AS "reportAsOf",
             d.official_pdf_url AS "pdfUrl",
             coalesce(pr.published_benchmark,rb.benchmark_name) AS "benchmarkName",
             er.ratio_percent::text AS "expenseRatioPercent",er.ratio_type AS "expenseRatioType",
             coalesce(er.as_of_date,r.portfolio_as_of,r.report_month)::text AS "expenseRatioAsOf",
             a.net_aum_cr::text AS "netAumCr",
             coalesce(a.aum_as_of,r.portfolio_as_of)::text AS "aumAsOf",
             coalesce(pr.launch_date,st.launch_date)::text AS "launchDate",
             coalesce((SELECT jsonb_agg(jsonb_build_object(
               'name',fm.manager_name,'role',rfm.manager_role,
               'managingSince',rfm.managing_since::text) ORDER BY fm.manager_name,rfm.manager_role)
               FROM public.sif_report_fund_managers rfm
               JOIN public.sif_fund_managers fm USING(manager_id)
               WHERE rfm.report_id=r.report_id),'[]'::jsonb) AS managers
      FROM public.sif_strategies st
      JOIN LATERAL (
        SELECT report.* FROM public.sif_factsheet_reports report
        JOIN public.sif_factsheet_documents doc ON doc.document_id=report.document_id
        WHERE report.strategy_id=st.strategy_id AND report.is_primary
          AND doc.official_only IS TRUE AND doc.amc_id=st.amc_id
          AND report.validation_status IN ('PASSED','WARNING')
          AND coalesce(report.portfolio_as_of,report.report_month) <= CURRENT_DATE
        ORDER BY report.report_month DESC,report.extracted_at DESC,report.report_id DESC LIMIT 1
      ) r ON true
      JOIN public.sif_factsheet_documents d ON d.document_id=r.document_id
      LEFT JOIN public.sif_report_strategy_profile pr ON pr.report_id=r.report_id
      LEFT JOIN public.sif_report_risk_bands rb ON rb.report_id=r.report_id
      LEFT JOIN public.sif_report_aum a ON a.report_id=r.report_id
      LEFT JOIN LATERAL (
        SELECT expense.* FROM public.sif_report_expense_ratios expense
        WHERE expense.report_id=r.report_id AND expense.plan_type='REGULAR'
          AND expense.ratio_type IN ('TER','BASE')
        ORDER BY CASE expense.ratio_type WHEN 'TER' THEN 0 ELSE 1 END LIMIT 1
      ) er ON true
      WHERE st.slug=$1 AND st.is_active
    `, [slug])
  ]);
  return { fund, schemes: schemes.rows, aaum: aaum.rows, source: source.rows[0] ?? null,
    profile: profile.rows[0] ?? null };
}
