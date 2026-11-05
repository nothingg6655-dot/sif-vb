"""Read-only reconciliation of every extracted factsheet with its stored report."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from database.migrate import verify_target
from .config import DatabaseConfig, FactsheetConfig
from .date_utils import parse_reporting_month, parse_portfolio_as_of
from .normalization import allocation_rows, clean_numeric, idcw_rows, performance_rows, quant_rows


def verify_storage(source_dir: Path | None = None) -> dict:
    base = Path(source_dir).resolve() if source_dir else FactsheetConfig.from_env().data_dir / 'latest'
    files = sorted((base / 'extraction').glob('*.json'))
    if not files:
        raise ValueError(f'No extracted factsheets found in {base / "extraction"}')
    result = {'reports_checked': 0, 'checks': 0, 'mismatches': [], 'sections': {}}

    def check(ok, name, field):
        result['checks'] += 1
        if not ok:
            result['mismatches'].append({'fund': name, 'field': field})

    def numeric_equal(actual, expected):
        expected = clean_numeric(expected)
        return actual is None if expected is None else actual is not None and abs(actual - expected) <= Decimal('0.000001')

    def count(section, total):
        result['sections'][section] = result['sections'].get(section, 0) + total

    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as conn:
        verify_target(conn)
        conn.execute('SET TRANSACTION READ ONLY')
        with conn.cursor(row_factory=dict_row) as cursor:
            def rows(query, params):
                return cursor.execute(query, params).fetchall()

            for path in files:
                for name, data in json.loads(path.read_text(encoding='utf-8')).items():
                    reports = rows("""
                        SELECT r.*,d.sha256,d.local_storage_uri FROM public.sif_factsheet_reports r
                        JOIN public.sif_factsheet_documents d USING(document_id)
                        WHERE r.published_scheme_name=%s AND d.sha256=%s AND r.is_primary
                    """, (name, data['Factsheet Source']['SHA256']))
                    check(len(reports) == 1, name, 'report identity')
                    if len(reports) != 1:
                        continue
                    report = reports[0]
                    report_id = report['report_id']
                    result['reports_checked'] += 1
                    check(report['raw_extraction_json'] == data, name, 'complete source JSON')
                    pdf = Path(report['local_storage_uri'])
                    check(pdf.is_file() and hashlib.sha256(pdf.read_bytes()).hexdigest() == report['sha256'], name, 'PDF checksum')
                    check(report['report_month'] == parse_reporting_month(data['Factsheet Source']['Reporting Month']), name, 'report month')
                    check(report['portfolio_as_of'] == parse_portfolio_as_of(data['Portfolio Date']), name, 'portfolio date')

                    risk = rows('SELECT * FROM public.sif_report_risk_bands WHERE report_id=%s', (report_id,))
                    expected_risk = data.get('Risk Band & Strategy') or {}
                    check(len(risk) == 1, name, 'risk band row')
                    if risk:
                        for field, column in [('Investment Strategy Risk Band', 'investment_strategy_risk_band'),
                                              ('Benchmark Risk Band', 'benchmark_risk_band'),
                                              ('Benchmark', 'benchmark_name')]:
                            check(risk[0][column] == (expected_risk.get(field) or None), name, field)
                        check(risk[0]['attributes'] == expected_risk, name, 'risk band attributes')
                    count('risk_bands', len(risk))

                    stored = {(r['allocation_family'], r['bucket_label']): r for r in rows(
                        'SELECT * FROM public.sif_report_allocations WHERE report_id=%s', (report_id,),
                    )}
                    expected = allocation_rows(data)
                    check(len(stored) == len(expected), name, 'allocation row count')
                    for value in expected:
                        actual = stored.get((value['family'], value['label']))
                        check(actual is not None and numeric_equal(actual['exposure_percent'], value['percent'])
                              and actual['extra'] == value['extra'] and actual['source_page'] == value['page'],
                              name, f"{value['family']}/{value['label']}")
                    count('allocations', len(expected))

                    stored = {(r['portfolio_section'], r['row_number']): r for r in rows(
                        'SELECT * FROM public.sif_report_holdings WHERE report_id=%s', (report_id,),
                    )}
                    expected = [(section, i, holding) for field, section in
                                [('Portfolio', 'MAIN'), ('Derivative Portfolio', 'DERIVATIVE')]
                                for i, holding in enumerate(data.get(field) or [], 1) if holding.get('Instrument')]
                    check(len(stored) == len(expected), name, 'holding row count')
                    for section, i, value in expected:
                        actual = stored.get((section, i))
                        check(actual is not None, name, f'{section} holding {i}')
                        if actual is None:
                            continue
                        for source, target in [
                            ('Instrument', 'published_instrument_name'), ('Source Page', 'source_page'),
                            ('Market Capitalization', 'market_cap_class'), ('Sector', 'sector_name'),
                            ('Rating', 'rating'), ('Rating / Classification', 'rating_classification'),
                            ('Derivative Type', 'derivative_type'), ('Asset Class', 'asset_class'),
                        ]:
                            check(actual[target] == (value.get(source) or None), name, f'{section} {i}/{source}')
                        for source, target in [
                            ('Quantity', 'quantity'), ('Market Value (INR Lakhs)', 'market_value_inr_lakhs'),
                            ('Percent to Net Assets', 'percent_to_net_assets'),
                            ('Derivatives Percent to Net Assets', 'derivative_exposure_pct'),
                            ('Hedged Percent to Net Assets', 'hedged_exposure_pct'),
                            ('Unhedged Percent to Net Assets', 'unhedged_exposure_pct'),
                            ('Published Net Exposure (%)', 'published_net_exposure_pct'),
                        ]:
                            check(numeric_equal(actual[target], value.get(source)), name, f'{section} {i}/{source}')
                    count('holdings', len(expected))

                    profile = rows('SELECT * FROM public.sif_report_strategy_profile WHERE report_id=%s', (report_id,))
                    check(len(profile) == 1 and profile[0]['supplementary_fields'] == data['Fund Details'], name, 'published fund details')
                    aum = rows('SELECT * FROM public.sif_report_aum WHERE report_id=%s', (report_id,))
                    check(len(aum) == 1 and aum[0]['supplementary_fields'] == data['AUM (INR Cr)'], name, 'published AUM')
                    if aum:
                        for field in ['Net AUM', 'Month End AUM', 'Published Fund Size']:
                            if data['AUM (INR Cr)'].get(field) is not None:
                                check(numeric_equal(aum[0]['net_aum_cr'], data['AUM (INR Cr)'][field]), name, field)
                                break
                        check(numeric_equal(aum[0]['monthly_average_aum_cr'], data['AUM (INR Cr)'].get('Monthly Average AUM')), name, 'Monthly Average AUM')

                    for field, table, label_column, value_column in [
                        ('NAV', 'sif_report_nav', 'published_label', 'nav'),
                    ]:
                        stored_values = {r[label_column]: r[value_column] for r in rows(
                            f'SELECT * FROM public.{table} WHERE report_id=%s', (report_id,),
                        )}
                        for label, value in data.get(field, {}).items():
                            if clean_numeric(value) is not None:
                                check(numeric_equal(stored_values.get(label), value), name, f'{field}/{label}')

                    expected = quant_rows(data)
                    stored = rows('SELECT * FROM public.sif_report_statistical_indicators WHERE report_id=%s', (report_id,))
                    check(len(stored) == len(expected), name, 'quant row count')
                    for value in expected:
                        check(any(r['family'] == value['family'] and r['indicator_name'] == value['name']
                                  and numeric_equal(r['numeric_value'], value['numeric'])
                                  and r['attributes'] == value['attributes'] for r in stored), name, value['name'])
                    count('quant_indicators', len(expected))

                    expected = performance_rows(data)
                    stored = rows('SELECT * FROM public.sif_report_published_performance WHERE report_id=%s', (report_id,))
                    check(len(stored) == len(expected), name, 'performance row count')
                    for value in expected:
                        check(any(r['additional_values'] == value['extra'] and r['performance_family'] == value['family']
                                  and numeric_equal(r['scheme_return_pct'], value['scheme_return'])
                                  and numeric_equal(r['benchmark_return_pct'], value['benchmark_return']) for r in stored),
                              name, f"published performance/{value['period']}/{value['plan']}")
                    count('published_performance', len(expected))

                    expected = idcw_rows(data)
                    stored = rows('SELECT * FROM public.sif_report_idcw_history WHERE report_id=%s', (report_id,))
                    check(len(stored) == len(expected), name, 'IDCW row count')
                    for value in expected:
                        check(any(r['event_date'] == value['date'] and r['plan_label'] == value['plan']
                                  and numeric_equal(r['amount_per_unit_inr'], value['amount'])
                                  and r['attributes'] == value['attributes'] for r in stored), name, f"IDCW/{value['date']}/{value['plan']}")
                    count('idcw_events', len(expected))

                    links = rows('SELECT * FROM public.sif_report_external_field_links WHERE report_id=%s', (report_id,))
                    check(len(links) == len(data.get('External Field Links') or []), name, 'external links')
                    count('external_links', len(links))
                    extensions = rows('SELECT field_key,raw_value FROM public.sif_report_extension_data WHERE report_id=%s', (report_id,))
                    for extension in extensions:
                        check(data.get(extension['field_key']) == extension['raw_value'], name, extension['field_key'])
                    count('extension_sections', len(extensions))
                    availability = rows('SELECT * FROM public.sif_report_field_availability WHERE report_id=%s', (report_id,))
                    check(len(availability) == len(data.get('Field Availability') or {}), name, 'field availability count')
                    for value in availability:
                        check(value['extra']['published_status'] == data['Field Availability'][value['field_path']]
                              and value['availability_status'] != 'UNKNOWN', name, f"availability/{value['field_path']}")
                    for page in (data.get('Published Pages') or []) + (data.get('Shared Annexure Pages') or []):
                        stored = rows('SELECT * FROM public.sif_factsheet_pages WHERE document_id=%s AND page_number=%s',
                                      (report['document_id'], page['Page']))
                        check(len(stored) == 1 and stored[0]['extracted_text'] == page.get('Text')
                              and stored[0]['extracted_links'] == (page.get('Links') or []), name, f"page/{page['Page']}")
    result['status'] = 'PASSED' if not result['mismatches'] else 'FAILED'
    return result
