import type pg from 'pg';

export interface ComparisonMetadata {
  fundId: number; fullName: string; slug: string; amc: string | null;
  strategy: string | null; category: string | null; launchDate: string | null;
  netAumCr: string | null; aumAsOf: string | null; riskBand: string | null;
  riskAsOf: string | null; returnProfile: string | null; pdfUrl: string | null;
}

export async function readComparisonMetadata(pool: pg.Pool, toDate: string) {
  return (await pool.query<ComparisonMetadata>(`
    SELECT f.fund_id::integer AS "fundId",f.canonical_name AS "fullName",f.slug,
           amc.amc_name AS amc,st.strategy_subcategory AS strategy,
           st.strategy_category AS category,
           coalesce(st.launch_date,pr.launch_date)::text AS "launchDate",
           a.net_aum_cr::text AS "netAumCr",
           coalesce(a.aum_as_of,r.portfolio_as_of)::text AS "aumAsOf",
           rb.investment_strategy_risk_band AS "riskBand",
           coalesce(rb.attributes->>'Risk Band As Of',r.portfolio_as_of::text) AS "riskAsOf",
           pr.supplementary_fields->>'Return Profile' AS "returnProfile",
           d.official_pdf_url AS "pdfUrl"
    FROM public.app_performance_config c
    JOIN public.app_funds f USING(fund_id)
    JOIN public.tigzig_schemes selected
      ON (selected.source,selected.scheme_code)=(c.preferred_source,c.preferred_scheme_code)
    LEFT JOIN public.sif_strategies st ON st.slug=f.slug AND st.is_active
    LEFT JOIN public.sif_amcs amc ON amc.amc_id=st.amc_id
    LEFT JOIN LATERAL (
      SELECT report.* FROM public.sif_factsheet_reports report
      JOIN public.sif_factsheet_documents doc ON doc.document_id=report.document_id
      WHERE report.strategy_id=st.strategy_id AND report.is_primary
        AND doc.official_only IS TRUE AND doc.amc_id=st.amc_id
        AND report.validation_status IN ('PASSED','WARNING')
        AND report.report_month <= $1::date
        AND coalesce(report.portfolio_as_of,report.report_month) <= $1::date
      ORDER BY report.report_month DESC,report.extracted_at DESC,report.report_id DESC LIMIT 1
    ) r ON true
    LEFT JOIN public.sif_factsheet_documents d ON d.document_id=r.document_id
    LEFT JOIN public.sif_report_aum a ON a.report_id=r.report_id
    LEFT JOIN public.sif_report_risk_bands rb ON rb.report_id=r.report_id
    LEFT JOIN public.sif_report_strategy_profile pr ON pr.report_id=r.report_id
    WHERE c.selection_verified AND c.mapping_confidence IN ('high','medium') AND f.status='active'
      AND selected.scheme_plan='Regular' AND selected.scheme_option='Growth'
    ORDER BY c.reference_fund_id
  `, [toDate])).rows;
}
