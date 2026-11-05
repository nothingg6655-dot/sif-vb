import type pg from 'pg';

export interface RiskBandRow {
  strategyId: number;
  fullName: string;
  slug: string;
  reportId: number | null;
  reportMonth: string | null;
  portfolioAsOf: string | null;
  riskBandAsOf: string | null;
  investmentStrategyRiskBand: string | null;
  benchmarkRiskBand: string | null;
  benchmarkName: string | null;
  pdfUrl: string | null;
  validationStatus: string | null;
  latestListingVerified: boolean | null;
}

export async function readRiskBandByName(pool: pg.Pool, nameKey: string): Promise<RiskBandRow[]> {
  return (await pool.query<RiskBandRow>(`
    SELECT lr.strategy_id::integer AS "strategyId",lr.strategy_name AS "fullName",lr.slug,
           lr.report_id::integer AS "reportId",to_char(lr.report_month,'YYYY-MM') AS "reportMonth",
           lr.portfolio_as_of::text AS "portfolioAsOf",
           rb.attributes->>'Risk Band As Of' AS "riskBandAsOf",
           rb.investment_strategy_risk_band AS "investmentStrategyRiskBand",
           rb.benchmark_risk_band AS "benchmarkRiskBand",
           coalesce(rb.benchmark_name,lr.published_benchmark) AS "benchmarkName",
           d.official_pdf_url AS "pdfUrl",lr.validation_status AS "validationStatus",
           d.latest_listing_verified AS "latestListingVerified"
    FROM public.vw_sif_strategy_latest_report lr
    LEFT JOIN public.sif_report_risk_bands rb ON rb.report_id=lr.report_id
    LEFT JOIN public.sif_factsheet_reports r ON r.report_id=lr.report_id
    LEFT JOIN public.sif_factsheet_documents d ON d.document_id=r.document_id
    WHERE regexp_replace(lower(lr.strategy_name),'[^a-z0-9]','','g')=$1
       OR EXISTS (
         SELECT 1 FROM public.vw_sif_verified_strategy_schemes s
         WHERE s.strategy_id=lr.strategy_id
           AND regexp_replace(lower(s.scheme_name),'[^a-z0-9]','','g')=$1
       )
    ORDER BY lr.strategy_id
  `, [nameKey])).rows;
}
