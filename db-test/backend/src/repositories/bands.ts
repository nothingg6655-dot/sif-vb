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
           coalesce(rb.benchmark_name,pr.published_benchmark) AS "benchmarkName",
           d.official_pdf_url AS "pdfUrl",lr.validation_status AS "validationStatus",
           d.latest_listing_verified AS "latestListingVerified"
    FROM (
      SELECT st.strategy_id,st.strategy_name,st.slug,
             report.report_id,report.report_month,report.portfolio_as_of,report.validation_status
      FROM public.sif_strategies st
      LEFT JOIN LATERAL (
        SELECT rpt.* FROM public.sif_factsheet_reports rpt
        JOIN public.sif_factsheet_documents doc ON doc.document_id=rpt.document_id
        WHERE rpt.strategy_id=st.strategy_id AND rpt.is_primary
          AND doc.official_only IS TRUE AND doc.amc_id=st.amc_id
          AND rpt.validation_status IN ('PASSED','WARNING')
          AND coalesce(rpt.portfolio_as_of,rpt.report_month) <= CURRENT_DATE
        ORDER BY rpt.report_month DESC,rpt.extracted_at DESC,rpt.report_id DESC LIMIT 1
      ) report ON true
    ) lr
    LEFT JOIN public.sif_report_risk_bands rb ON rb.report_id=lr.report_id
    LEFT JOIN public.sif_report_strategy_profile pr ON pr.report_id=lr.report_id
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
