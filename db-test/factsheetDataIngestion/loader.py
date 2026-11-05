"""Idempotent database loader for extracted factsheet JSON and PDF documents."""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from database.migrate import verify_target
from .config import DatabaseConfig, FactsheetConfig, SOURCE_CODE, DATASET_NAME
from .date_utils import parse_reporting_month, parse_portfolio_as_of, parse_flexible_date
from .fund_registry import FUND_HOUSES, canonical_fund_house, normalize_name, slugify
from .strategy_manager import StrategyManager
from .normalization import (
    clean_numeric as _clean_numeric, profile_values, allocation_rows,
    availability_status, quant_rows, performance_rows, idcw_rows,
)

LOG = logging.getLogger('factsheet-loader')


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


@dataclass
class IngestionReport:
    run_id: int
    started_at: datetime
    finished_at: datetime | None = None
    status: str = 'RUNNING'
    documents_ingested: int = 0
    reports_ingested: int = 0
    holdings_ingested: int = 0
    allocations_ingested: int = 0
    errors: list[str] = field(default_factory=list)


class FactsheetIngester:
    """Loads extracted factsheet JSON files and PDF document records into PostgreSQL."""

    def __init__(self, connection: psycopg.Connection, config: FactsheetConfig | None = None):
        self.connection = connection
        verify_target(self.connection)
        self.config = config or FactsheetConfig.from_env()
        self.strategy_manager = StrategyManager(self.connection)

    def _start_run(self, metadata: dict[str, Any]) -> int:
        row = self.connection.execute("""
            INSERT INTO public.sif_ingestion_runs(
                source_code, dataset, started_at, status, metadata
            ) VALUES (%s, %s, NOW(), 'RUNNING', %s)
            RETURNING run_id
        """, (SOURCE_CODE, DATASET_NAME, Jsonb(metadata))).fetchone()
        return row[0]

    def _finish_run(self, run_id: int, status: str, counts: dict[str, int], error_summary: str | None = None):
        self.connection.execute("""
            UPDATE public.sif_ingestion_runs
            SET finished_at = NOW(),
                status = %s,
                fetched_count = %s,
                inserted_count = %s,
                updated_count = %s,
                rejected_count = %s,
                error_summary = %s
            WHERE run_id = %s
        """, (
            status,
            counts.get('fetched', 0),
            counts.get('inserted', 0),
            counts.get('updated', 0),
            counts.get('rejected', 0),
            error_summary,
            run_id,
        ))

    def _ensure_instrument(self, name: str) -> int:
        canonical = name.strip()
        # canonical_name has no unique constraint; ON CONFLICT alone never reuses it.
        existing = self.connection.execute(
            'SELECT instrument_id FROM public.sif_instruments '
            'WHERE canonical_name=%s ORDER BY instrument_id LIMIT 1', (canonical,),
        ).fetchone()
        if existing:
            return existing[0]
        row = self.connection.execute("""
            INSERT INTO public.sif_instruments(canonical_name)
            VALUES (%s)
            ON CONFLICT DO NOTHING
            RETURNING instrument_id
        """, (canonical,)).fetchone()
        if row:
            return row[0]
        row = self.connection.execute("""
            SELECT instrument_id FROM public.sif_instruments WHERE canonical_name = %s LIMIT 1
        """, (canonical,)).fetchone()
        return row[0] if row else 0

    def _ensure_manager(self, name: str) -> int:
        clean_name = name.strip()
        row = self.connection.execute("""
            INSERT INTO public.sif_fund_managers(manager_name)
            VALUES (%s)
            ON CONFLICT (manager_name) DO UPDATE SET manager_name = EXCLUDED.manager_name
            RETURNING manager_id
        """, (clean_name,)).fetchone()
        return row[0]

    def _upsert_document(self, run_id: int, amc_id: int, file_info: dict[str, Any]) -> int:
        sha256 = file_info['sha256'].lower()
        file_name = file_info.get('file_name', 'factsheet.pdf')
        pdf_url = file_info.get('pdf_url')
        local_path = file_info.get('local_path')
        size_bytes = file_info.get('size_bytes')
        reporting_month = file_info.get('reporting_month')
        latest_verified = file_info.get('latest_listing_verified', False)

        # 1. Register in sif_ingestion_files
        file_row = self.connection.execute("""
            INSERT INTO public.sif_ingestion_files(
                source_code, run_id, file_name, original_url, storage_uri,
                sha256, mime_type, bytes_size, retrieved_at
            ) VALUES (%s, %s, %s, %s, %s, %s, 'application/pdf', %s, NOW())
            ON CONFLICT (source_code, sha256) DO UPDATE SET
                run_id = EXCLUDED.run_id,
                file_name = EXCLUDED.file_name,
                original_url = EXCLUDED.original_url,
                storage_uri = EXCLUDED.storage_uri,
                bytes_size = EXCLUDED.bytes_size,
                retrieved_at = NOW()
            RETURNING file_id
        """, (SOURCE_CODE, run_id, file_name, pdf_url, local_path, sha256, size_bytes)).fetchone()
        file_id = file_row[0]

        # 2. Register in sif_factsheet_documents
        doc_row = self.connection.execute("""
            INSERT INTO public.sif_factsheet_documents(
                amc_id, ingestion_file_id, publishing_page_url, official_pdf_url,
                local_storage_uri, sha256, reporting_month, edition_name,
                official_only, latest_listing_verified, downloaded_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, NOW())
            ON CONFLICT (sha256) DO UPDATE SET
                amc_id = EXCLUDED.amc_id,
                ingestion_file_id = EXCLUDED.ingestion_file_id,
                publishing_page_url = EXCLUDED.publishing_page_url,
                official_pdf_url = EXCLUDED.official_pdf_url,
                local_storage_uri = EXCLUDED.local_storage_uri,
                reporting_month = EXCLUDED.reporting_month,
                edition_name = EXCLUDED.edition_name,
                latest_listing_verified = EXCLUDED.latest_listing_verified,
                downloaded_at = NOW()
            RETURNING document_id
        """, (
            amc_id, file_id, file_info.get('page_url'), pdf_url, local_path,
            sha256, reporting_month, file_info.get('edition_name'), latest_verified
        )).fetchone()
        return doc_row[0]

    def _ingest_scheme_report(
        self,
        document_id: int,
        strategy_id: int,
        scheme_name: str,
        data: dict[str, Any],
        reporting_month: date,
        portfolio_as_of: date | None,
    ) -> int:
        val_info = data.get('Extraction Validation', {})
        val_status_raw = val_info.get('Status', 'pending').upper()
        if 'PASS' in val_status_raw:
            val_status = 'PASSED' if val_status_raw == 'PASSED' else 'WARNING'
        elif 'FAIL' in val_status_raw:
            val_status = 'FAILED'
        else:
            val_status = 'PENDING'

        adapter_name = val_info.get('Adapter', 'generic_sif_adapter')
        scope = data.get('Portfolio Scope', 'Published holdings')
        raw_source_pages = data.get('Source Pages') or []
        source_pages = [
            p if isinstance(p, int) else p.get('Page')
            for p in raw_source_pages
            if isinstance(p, int) or (isinstance(p, dict) and isinstance(p.get('Page'), int))
        ]

        raw_annexure_pages = data.get('Shared Annexure Pages') or []
        annexure_pages = [
            p if isinstance(p, int) else p.get('Page')
            for p in raw_annexure_pages
            if isinstance(p, int) or (isinstance(p, dict) and isinstance(p.get('Page'), int))
        ]

        # If an earlier primary report exists for this (strategy_id, reporting_month),
        # unmark its primary flag first so the partial index uq_sif_primary_strategy_month is satisfied
        self.connection.execute("""
            UPDATE public.sif_factsheet_reports
            SET is_primary = FALSE
            WHERE strategy_id = %s AND report_month = %s AND is_primary = TRUE
        """, (strategy_id, reporting_month))

        # Insert or update the report
        report_row = self.connection.execute("""
            INSERT INTO public.sif_factsheet_reports(
                document_id, strategy_id, report_month, portfolio_as_of,
                section_key, plan_edition, published_scheme_name,
                source_pages, annexure_pages, portfolio_scope,
                is_primary, extraction_adapter, validation_status,
                validation_checks, validation_errors, validation_warnings,
                raw_extraction_json, extracted_at
            ) VALUES (
                %s, %s, %s, %s, 'MAIN', 'COMBINED', %s,
                %s, %s, %s, TRUE, %s, %s,
                %s, %s, %s, %s, NOW()
            )
            ON CONFLICT (document_id, strategy_id, section_key, plan_edition) DO UPDATE SET
                report_month = EXCLUDED.report_month,
                portfolio_as_of = EXCLUDED.portfolio_as_of,
                published_scheme_name = EXCLUDED.published_scheme_name,
                source_pages = EXCLUDED.source_pages,
                annexure_pages = EXCLUDED.annexure_pages,
                portfolio_scope = EXCLUDED.portfolio_scope,
                is_primary = TRUE,
                extraction_adapter = EXCLUDED.extraction_adapter,
                validation_status = EXCLUDED.validation_status,
                validation_checks = EXCLUDED.validation_checks,
                validation_errors = EXCLUDED.validation_errors,
                validation_warnings = EXCLUDED.validation_warnings,
                raw_extraction_json = EXCLUDED.raw_extraction_json,
                extracted_at = NOW()
            RETURNING report_id
        """, (
            document_id, strategy_id, reporting_month, portfolio_as_of,
            scheme_name, source_pages, annexure_pages, scope,
            adapter_name, val_status,
            Jsonb(val_info.get('Checks', {})),
            Jsonb(val_info.get('Errors', [])),
            Jsonb(val_info.get('Warnings', [])),
            Jsonb(data),
        )).fetchone()
        report_id = report_row[0]

        # Clean existing report children (CASCADE or explicit delete)
        self.connection.execute("DELETE FROM public.sif_report_strategy_profile WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_fund_managers WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_aum WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_nav WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_expense_ratios WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_holdings WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_allocations WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_statistical_indicators WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_risk_bands WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_published_performance WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_idcw_history WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_notes WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_external_field_links WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_field_availability WHERE report_id = %s", (report_id,))
        self.connection.execute("DELETE FROM public.sif_report_extension_data WHERE report_id = %s", (report_id,))

        # 1. Strategy profile
        fund_details = data.get('Fund Details', {})
        profile = profile_values(data)

        self.connection.execute("""
            INSERT INTO public.sif_report_strategy_profile(
                report_id, investment_objective, launch_date, published_benchmark,
                load_structure, minimum_investment_inr, accredited_minimum_inr,
                subscription_redemption_frequency, taxation, portfolio_turnover_ratio,
                strategy_description, supplementary_fields
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            report_id,
            _clean_text(profile['investment_objective']),
            profile['launch_date'],
            _clean_text(profile['published_benchmark']),
            _clean_text(profile['load_structure']),
            profile['minimum_investment_inr'], profile['accredited_minimum_inr'],
            _clean_text(profile['subscription_redemption_frequency']),
            _clean_text(profile['taxation']),
            profile['portfolio_turnover_ratio'],
            _clean_text(profile['strategy_description']),
            Jsonb(fund_details),
        ))

        # 2. Fund Managers
        for mgr in data.get('Fund Managers', []):
            name = mgr.get('Name')
            if not name:
                continue
            mgr_id = self._ensure_manager(name)
            self.connection.execute("""
                INSERT INTO public.sif_report_fund_managers(
                    report_id, manager_id, manager_role, experience_text,
                    managing_since, published_footnote, supplementary_fields
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (report_id, manager_id, manager_role) DO NOTHING
            """, (
                report_id, mgr_id,
                mgr.get('Role') or 'Fund Manager',
                _clean_text(mgr.get('Experience')),
                parse_flexible_date(mgr.get('Managing Since')),
                _clean_text(mgr.get('Published Footnote')),
                Jsonb(mgr.get('Supplementary Fields', {})),
            ))

        # 3. AUM
        aum = data.get('AUM (INR Cr)', {})
        net_aum = next((_clean_numeric(aum[key]) for key in
                        ('Net AUM', 'Month End AUM', 'Published Fund Size')
                        if aum.get(key) is not None), None)
        avg_aum = _clean_numeric(aum.get('Monthly Average AUM'))
        if net_aum is not None or avg_aum is not None or aum:
            self.connection.execute("""
                INSERT INTO public.sif_report_aum(
                    report_id, aum_as_of, net_aum_cr, monthly_average_aum_cr, supplementary_fields
                ) VALUES (%s, %s, %s, %s, %s)
            """, (report_id, portfolio_as_of, net_aum, avg_aum, Jsonb(aum)))

        # 4. NAV
        for label, val in data.get('NAV', {}).items():
            num = _clean_numeric(val)
            if num is None or num <= 0:
                continue
            lbl_lower = label.lower()
            plan = 'DIRECT' if 'dir' in lbl_lower else ('REGULAR' if 'reg' in lbl_lower else None)
            option = 'GROWTH' if 'growth' in lbl_lower else ('IDCW' if 'idcw' in lbl_lower else None)
            self.connection.execute("""
                INSERT INTO public.sif_report_nav(
                    report_id, published_label, plan_type, option_type, nav_date, nav
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (report_id, published_label) DO NOTHING
            """, (report_id, label, plan, option, portfolio_as_of, num))

        # 5. Expense Ratios
        for plan_lbl, val in data.get('Base Expense Ratio (%)', {}).items():
            num = _clean_numeric(val)
            if num is None or num < 0:
                continue
            lbl_lower = plan_lbl.lower()
            plan_type = 'DIRECT' if 'dir' in lbl_lower else ('REGULAR' if 'reg' in lbl_lower else 'OTHER')
            ratio_type = 'TER' if 'ter' in lbl_lower else 'BASE'
            self.connection.execute("""
                INSERT INTO public.sif_report_expense_ratios(
                    report_id, plan_type, ratio_type, ratio_percent, as_of_date
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (report_id, plan_type, ratio_type) DO NOTHING
            """, (report_id, plan_type, ratio_type, num, portfolio_as_of))

        # Preserve the same quantitative and provenance fields in both portfolios.
        holdings = [('MAIN', idx, h) for idx, h in enumerate(data.get('Portfolio', []), 1)]
        holdings += [('DERIVATIVE', idx, h) for idx, h in enumerate(data.get('Derivative Portfolio', []), 1)]
        for section, idx, h in holdings:
            inst_name = h.get('Instrument')
            if not inst_name:
                continue
            inst_id = self._ensure_instrument(inst_name)
            pct = _clean_numeric(h.get('Percent to Net Assets'))
            mval = _clean_numeric(h.get('Market Value (INR Lakhs)'))
            qty = _clean_numeric(h.get('Quantity'))
            deriv_pct = _clean_numeric(h.get('Derivatives Percent to Net Assets'))
            hedged_pct = _clean_numeric(h.get('Hedged Percent to Net Assets'))
            unhedged_pct = _clean_numeric(h.get('Unhedged Percent to Net Assets'))
            net_exp_pct = _clean_numeric(h.get('Published Net Exposure (%)'))

            self.connection.execute("""
                INSERT INTO public.sif_report_holdings(
                    report_id, row_number, portfolio_section, instrument_id,
                    published_instrument_name, asset_class, category_name,
                    sector_name, market_cap_class, rating, rating_classification,
                    derivative_type, quantity, market_value_inr_lakhs,
                    percent_to_net_assets, derivative_exposure_pct,
                    hedged_exposure_pct, unhedged_exposure_pct, published_net_exposure_pct,
                    is_aggregate, source_page, published_precision_marker, supplementary_fields
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (report_id, portfolio_section, row_number) DO NOTHING
            """, (
                report_id, idx, section, inst_id, inst_name,
                _clean_text(h.get('Asset Class')),
                _clean_text(h.get('Category')),
                _clean_text(h.get('Sector')),
                _clean_text(h.get('Market Capitalization')),
                _clean_text(h.get('Rating')),
                _clean_text(h.get('Rating / Classification')),
                _clean_text(h.get('Derivative Type')),
                qty, mval, pct, deriv_pct, hedged_pct, unhedged_pct, net_exp_pct,
                bool(h.get('Aggregate', False)),
                h.get('Source Page'),
                _clean_text(h.get('Published Precision Marker')),
                Jsonb(h.get('Supplementary Fields', {})),
            ))

        # 8. Allocations & Totals
        allocations = allocation_rows(data)
        for allocation in allocations:
            self.connection.execute("""
                INSERT INTO public.sif_report_allocations(
                    report_id, allocation_family, bucket_label, exposure_percent,
                    published_order, source_page, extra
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (report_id, allocation['family'], allocation['label'], allocation['percent'],
                  allocation['order'], allocation['page'], Jsonb(allocation['extra'])))

        # 9. Statistical debt & quant indicators
        for quant in quant_rows(data):
            self.connection.execute("""
                INSERT INTO public.sif_report_statistical_indicators(
                    report_id, family, indicator_name, numeric_value, text_value, unit, attributes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (report_id, quant['family'], quant['name'], quant['numeric'],
                  quant['text'], quant['unit'], Jsonb(quant['attributes'])))

        for performance in performance_rows(data):
            self.connection.execute("""
                INSERT INTO public.sif_report_published_performance(
                    report_id, performance_family, published_period_label, plan_label,
                    option_label, period_start, period_end, scheme_return_pct,
                    benchmark_return_pct, additional_values
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (report_id, performance['family'], performance['period'], performance['plan'],
                  performance['option'], performance['start'], performance['end'],
                  performance['scheme_return'], performance['benchmark_return'], Jsonb(performance['extra'])))

        for event in idcw_rows(data):
            self.connection.execute("""
                INSERT INTO public.sif_report_idcw_history(
                    report_id, event_date, plan_label, amount_per_unit_inr, attributes
                ) VALUES (%s,%s,%s,%s,%s)
            """, (report_id, event['date'], event['plan'], event['amount'], Jsonb(event['attributes'])))

        # 10. Risk bands
        risk = data.get('Risk Band & Strategy', {})
        if risk:
            self.connection.execute("""
                INSERT INTO public.sif_report_risk_bands(
                    report_id, investment_strategy_risk_band, benchmark_risk_band,
                    benchmark_name, attributes
                ) VALUES (%s, %s, %s, %s, %s)
            """, (
                report_id,
                _clean_text(risk.get('Investment Strategy Risk Band') or risk.get('Riskometer')),
                _clean_text(risk.get('Benchmark Risk Band')),
                _clean_text(risk.get('Benchmark')),
                Jsonb(risk),
            ))

        # 11. Notes
        for note in data.get('Published Portfolio Notes', []):
            if isinstance(note, str) and note.strip():
                self.connection.execute("""
                    INSERT INTO public.sif_report_notes(
                        report_id, note_family, note_text
                    ) VALUES (%s, 'PORTFOLIO', %s)
                """, (report_id, note.strip()))

        # 12. Field availability
        for fpath, avail in data.get('Field Availability', {}).items():
            normalized_status = availability_status(avail)
            if normalized_status == 'PARSE_FAILED' and fpath == 'Asset Allocation (%)' \
                    and any(row['family'] == 'ASSET' and row['percent'] is not None for row in allocations):
                normalized_status = 'PUBLISHED'
            self.connection.execute("""
                INSERT INTO public.sif_report_field_availability(
                    report_id, field_path, availability_status, extra
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (report_id, field_path) DO NOTHING
            """, (report_id, fpath, normalized_status, Jsonb({'published_status': avail})))

        for link in data.get('External Field Links') or []:
            info = link if isinstance(link, dict) else {'URL': link}
            self.connection.execute("""
                INSERT INTO public.sif_report_external_field_links(
                    report_id, published_field, target_field, source_name, external_url, extra
                ) VALUES (%s,%s,%s,%s,%s,%s)
            """, (report_id, info.get('Published Field'), info.get('Target Field'),
                  info.get('Source Name'), info.get('URL') or info.get('url'), Jsonb(info)))

        # Retain AMC-specific sections and evidence even when no scalar mapping exists.
        for field in ('Published Portfolio Rows', 'Published Pages', 'Shared Annexure Pages',
                      'Published Asset Allocation Rows', 'Published Derivative and Cash Rows',
                      'Published Debt Category Rows', 'Published Other Exposure Rows',
                      'Published Portfolio Quants', 'Published AUM Corrections',
                      'Additional Factsheet Editions', 'Supplementary Fields'):
            value = data.get(field)
            if value:
                self.connection.execute("""
                    INSERT INTO public.sif_report_extension_data(report_id,field_group,field_key,raw_value)
                    VALUES (%s,'EXTRACTION',%s,%s)
                """, (report_id, field, Jsonb(value)))

        for page in (data.get('Published Pages') or []) + (data.get('Shared Annexure Pages') or []):
            if isinstance(page, dict) and isinstance(page.get('Page'), int):
                self.connection.execute("""
                    INSERT INTO public.sif_factsheet_pages(document_id,page_number,extracted_text,extracted_links)
                    VALUES (%s,%s,%s,%s)
                    ON CONFLICT(document_id,page_number) DO UPDATE SET
                        extracted_text=EXCLUDED.extracted_text, extracted_links=EXCLUDED.extracted_links
                """, (document_id, page['Page'], page.get('Text'), Jsonb(page.get('Links') or [])))

        return report_id

    def _load_scheme(
        self, run_id: int, scheme_name: str, data: dict, amc_ids: dict,
        strat_ids: dict, manifest_files: dict, pdfs_dir: Path | None,
        document_cache: dict,
    ) -> tuple[str, int, int, bool]:
        house_name = data.get('AMC / Fund House') or canonical_fund_house(scheme_name)
        if house_name not in amc_ids or canonical_fund_house(scheme_name) != house_name:
            raise ValueError(f'Unknown or conflicting AMC: {house_name}')
        strategy_id = next((sid for name, sid in strat_ids.items()
                            if normalize_name(name) == normalize_name(scheme_name)), None)
        if strategy_id is None:
            raise ValueError('No matching canonical strategy')
        validation = (data.get('Extraction Validation') or {}).get('Status', '').lower()
        if validation not in {'passed', 'passed_with_warnings'}:
            raise ValueError(f'Extraction has not passed validation: {validation or "missing"}')
        source = data.get('Factsheet Source') or {}
        digest = (source.get('SHA256') or '').lower()
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Missing or invalid source PDF SHA256; a JSON hash is not a PDF identity')
        period = parse_reporting_month(source.get('Reporting Month') or data.get('Portfolio Date'))
        if period is None:
            raise ValueError('Missing or invalid reporting month')
        as_of = parse_portfolio_as_of(data.get('Portfolio Date'))
        if digest in document_cache:
            document_id = document_cache[digest]
        else:
            manifest = manifest_files.get(digest, {})
            pdf_name = manifest.get('File Name') or f'{house_name}.pdf'
            # Only select a direct child of the supplied source directory.
            if Path(pdf_name).name != pdf_name:
                raise ValueError('Manifest PDF name must not contain a directory')
            pdf = pdfs_dir / pdf_name if pdfs_dir else None
            if pdf is None or not pdf.is_file():
                pdf = Path(source.get('Local PDF') or '')
            if not pdf.is_file():
                raise ValueError('Source PDF is missing')
            if hashlib.sha256(pdf.read_bytes()).hexdigest() != digest:
                raise ValueError('Source PDF checksum does not match extraction metadata')
            document_id = self._upsert_document(run_id, amc_ids[house_name], {
                'sha256': digest, 'file_name': pdf.name,
                'pdf_url': source.get('PDF URL') or manifest.get('PDF URL'),
                'page_url': source.get('Publishing Page'),
                'local_path': str(pdf.resolve()), 'size_bytes': pdf.stat().st_size,
                'reporting_month': period, 'edition_name': f'{house_name} Factsheet',
                'latest_listing_verified': manifest.get('Latest Listing Verified',
                                                        source.get('Latest Listing Verified', False)),
            })
        existing = self.connection.execute("""
            SELECT report_id FROM public.sif_factsheet_reports
            WHERE document_id=%s AND strategy_id=%s AND section_key='MAIN' AND plan_edition='COMBINED'
        """, (document_id, strategy_id)).fetchone() is not None
        report_id = self._ingest_scheme_report(
            document_id, strategy_id, scheme_name, data, period, as_of,
        )
        return digest, document_id, report_id, existing

    def _record_rejection(self, run_id: int, key: str, error: Exception, raw: Any):
        self.connection.execute("""
            INSERT INTO public.sif_ingestion_rejections(
                run_id,source_code,source_row_key,entity_kind,error_code,error_message,raw_row
            ) VALUES (%s,%s,%s,'FACTSHEET_REPORT',%s,%s,%s)
        """, (run_id, SOURCE_CODE, key, type(error).__name__, str(error), Jsonb(raw)))

    def load_factsheets(
        self,
        extraction_dir: Path,
        manifest_path: Path | None = None,
        pdfs_dir: Path | None = None,
    ) -> IngestionReport:
        """Load extracted factsheet JSON and PDF documents from directory into PostgreSQL."""
        # Serialize loaders so selecting/creating shared instruments is repeatable.
        self.connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('VB-SIF:FACTSHEETS',0))")
        # Ensure strategies, AMCs, schemes, and links are synchronized
        amc_ids = self.strategy_manager.sync_amcs()
        strat_ids = self.strategy_manager.sync_strategies(amc_ids)
        self.strategy_manager.sync_schemes_from_tigzig(amc_ids)
        self.strategy_manager.link_strategies_and_schemes(strat_ids)

        metadata = {'source_directory': str(extraction_dir)}
        run_id = self._start_run(metadata)
        report = IngestionReport(run_id=run_id, started_at=datetime.now(timezone.utc))

        # Read manifest if available
        manifest_files: dict[str, dict[str, Any]] = {}
        if manifest_path and manifest_path.is_file():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding='utf-8'))
                for fentry in manifest_data.get('Files', []):
                    digest = fentry.get('SHA256', '').lower()
                    if digest:
                        manifest_files[digest] = fentry
            except Exception as exc:
                report.errors.append(f'Failed to parse manifest: {exc}')

        json_files = sorted(extraction_dir.glob('*.json'))
        LOG.info("Found %d extracted JSON files in %s", len(json_files), extraction_dir)

        document_cache: dict[str, int] = {}
        inserted = updated = fetched = 0

        for jpath in json_files:
            try:
                content = json.loads(jpath.read_text(encoding='utf-8'))
                if not content or not isinstance(content, dict):
                    raise ValueError('Extraction must contain a nonempty scheme mapping')
            except Exception as exc:
                fetched += 1
                report.errors.append(f'{jpath.name}: {exc}')
                self._record_rejection(run_id, jpath.name, exc, {'filename': jpath.name})
                continue
            for scheme_name, data in content.items():
                fetched += 1
                try:
                    # A bad report must not discard a previous report's children or
                    # leave the transaction unusable for the remaining source files.
                    with self.connection.transaction():
                        if not isinstance(data, dict):
                            raise ValueError('Scheme extraction must be an object')
                        digest, doc_id, report_id, existed = self._load_scheme(
                            run_id, scheme_name, data, amc_ids, strat_ids,
                            manifest_files, pdfs_dir, document_cache,
                        )
                        holdings = self.connection.execute(
                            'SELECT count(*) FROM public.sif_report_holdings WHERE report_id=%s',
                            (report_id,),
                        ).fetchone()[0]
                        allocations = self.connection.execute(
                            'SELECT count(*) FROM public.sif_report_allocations WHERE report_id=%s',
                            (report_id,),
                        ).fetchone()[0]
                    if digest not in document_cache:
                        document_cache[digest] = doc_id
                        report.documents_ingested += 1
                    report.reports_ingested += 1
                    report.holdings_ingested += holdings
                    report.allocations_ingested += allocations
                    updated += int(existed)
                    inserted += int(not existed)
                except Exception as exc:
                    message = f'{jpath.name} / {scheme_name}: {exc}'
                    LOG.error(message)
                    report.errors.append(message)
                    self._record_rejection(run_id, scheme_name, exc, data)

        status = 'SUCCEEDED' if not report.errors else ('PARTIAL' if report.reports_ingested > 0 else 'FAILED')
        report.status = status
        report.finished_at = datetime.now(timezone.utc)
        self._finish_run(
            run_id=run_id,
            status=status,
            counts={
                'fetched': fetched,
                'inserted': inserted,
                'updated': updated,
                'rejected': len(report.errors),
            },
            error_summary='; '.join(report.errors[:5]) if report.errors else None,
        )

        return report


def ingest_factsheet_data(
    source_dir: Path | None = None,
    db_config: DatabaseConfig | None = None,
) -> IngestionReport:
    """Ingest factsheets from local extraction folder into VB-SIF database."""
    config = FactsheetConfig.from_env()
    base_dir = Path(source_dir).resolve() if source_dir else config.data_dir / 'latest'
    if not base_dir.is_dir() and (config.sif_source_dir / 'output' / 'latest').is_dir():
        base_dir = config.sif_source_dir / 'output' / 'latest'

    extraction_dir = base_dir / 'extraction'
    manifest_path = base_dir / 'manifest.json'
    pdfs_dir = base_dir

    if not extraction_dir.is_dir():
        raise FileNotFoundError(f"Extraction directory not found at {extraction_dir}")

    cfg = db_config or DatabaseConfig.from_env()
    with psycopg.connect(**cfg.connection_kwargs(), autocommit=True) as conn:
        with conn.transaction():
            ingester = FactsheetIngester(conn, config)
            report = ingester.load_factsheets(
                extraction_dir=extraction_dir,
                manifest_path=manifest_path if manifest_path.is_file() else None,
                pdfs_dir=pdfs_dir if pdfs_dir.is_dir() else None,
            )
            LOG.info("Factsheet ingestion report: %s", report)
            return report
