import type pg from 'pg';

export type AllocationFamily = 'ASSET' | 'MARKET_CAP' | 'SECTOR' | 'RATING';

export interface AllocationRow {
  strategyId: number;
  fullName: string;
  slug: string;
  reportId: number | null;
  reportMonth: string | null;
  portfolioAsOf: string | null;
  validationStatus: string | null;
  pdfUrl: string | null;
  latestListingVerified: boolean | null;
  availability: string | null;
  label: string | null;
  percent: string | null;
  basis: string | null;
  sourcePage: number | null;
  publishedOrder: number | null;
}

export async function readFundAllocation(
  pool: pg.Pool, slug: string | null, family: AllocationFamily, field: string,
): Promise<AllocationRow[]> {
  // One statement keeps the report metadata and its allocations on one snapshot.
  // Select only designated official reports belonging to this strategy's AMC.
  return (await pool.query<AllocationRow>(`${allocationSelectSql}
    WHERE ($1::text IS NULL OR lr.slug=$1)
    ${allocationOrderSql}
  `, [slug, family, field])).rows;
}

export async function readFundAllocationByName(
  pool: pg.Pool, nameKey: string, family: AllocationFamily, field: string,
): Promise<AllocationRow[]> {
  return (await pool.query<AllocationRow>(`${allocationSelectSql}
    WHERE regexp_replace(lower(lr.strategy_name),'[^a-z0-9]','','g')=$1
       OR EXISTS (
         SELECT 1 FROM public.vw_sif_verified_strategy_schemes s
         WHERE s.strategy_id=lr.strategy_id
           AND regexp_replace(lower(s.scheme_name),'[^a-z0-9]','','g')=$1
       )
    ${allocationOrderSql}
  `, [nameKey, family, field])).rows;
}

const allocationSelectSql = `
    SELECT lr.strategy_id::integer AS "strategyId",lr.strategy_name AS "fullName",lr.slug,
           lr.report_id::integer AS "reportId",
           to_char(lr.report_month,'YYYY-MM') AS "reportMonth",
           lr.portfolio_as_of::text AS "portfolioAsOf",
           lr.validation_status AS "validationStatus",
           d.official_pdf_url AS "pdfUrl",d.latest_listing_verified AS "latestListingVerified",
           fa.availability_status AS availability,
           a.bucket_label AS label,a.exposure_percent::text AS percent,
           a.extra->>'basis' AS basis,a.source_page AS "sourcePage",
           a.published_order AS "publishedOrder"
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
    LEFT JOIN public.sif_factsheet_reports r ON r.report_id=lr.report_id
    LEFT JOIN public.sif_factsheet_documents d ON d.document_id=r.document_id
    LEFT JOIN public.sif_report_field_availability fa
      ON fa.report_id=lr.report_id AND fa.field_path=$3
    LEFT JOIN public.sif_report_allocations a
      ON a.report_id=lr.report_id AND a.allocation_family=$2
`;

const allocationOrderSql = `
    ORDER BY lr.slug,lr.strategy_id,a.exposure_percent DESC NULLS LAST,
             a.published_order,a.bucket_label
`;
