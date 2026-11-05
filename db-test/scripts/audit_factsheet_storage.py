"""Read-only audit of local factsheet extractions and their PostgreSQL storage.

Run from the project root with .venv/Scripts/python.exe -m scripts.audit_factsheet_storage.
Writes evidence and a human-readable report under docs/reports; never updates PostgreSQL.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal
import hashlib
import html
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from database.migrate import verify_target
from factsheetDataIngestion.config import DatabaseConfig
from factsheetDataIngestion.date_utils import parse_flexible_date, parse_portfolio_as_of
from factsheetDataIngestion.normalization import (
    allocation_rows, clean_numeric, idcw_rows, performance_rows, profile_values, quant_rows,
)
from factsheetDataIngestion.verification import verify_storage

BASE = Path(__file__).resolve().parents[1]
SOURCE = BASE / 'data/factsheets/latest'
OUTPUT = BASE / 'docs/reports'
TABLES = (
    'sif_report_strategy_profile', 'sif_report_fund_managers', 'sif_report_aum',
    'sif_report_nav', 'sif_report_expense_ratios', 'sif_report_holdings',
    'sif_report_allocations', 'sif_report_statistical_indicators', 'sif_report_risk_bands',
    'sif_report_published_performance', 'sif_report_idcw_history', 'sif_report_notes',
    'sif_report_external_field_links', 'sif_report_field_availability',
    'sif_report_extension_data',
)
EXTENSIONS = (
    'Published Portfolio Rows', 'Published Pages', 'Shared Annexure Pages',
    'Published Asset Allocation Rows', 'Published Derivative and Cash Rows',
    'Published Debt Category Rows', 'Published Other Exposure Rows',
    'Published Portfolio Quants', 'Published AUM Corrections',
    'Additional Factsheet Editions', 'Supplementary Fields',
)
VOLATILE_SOURCE_FIELDS = {'Local PDF', 'Downloaded At', 'Latest Listing Verified'}


def clean_text(value):
    return str(value).strip() or None if value is not None else None


def equivalent(actual, expected):
    if isinstance(expected, Decimal):
        return actual is not None and abs(Decimal(str(actual)) - expected) <= Decimal('0.000001')
    return actual == expected


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    coverage = json.loads((SOURCE / 'coverage.json').read_text(encoding='utf-8'))
    manifest = json.loads((SOURCE / 'manifest.json').read_text(encoding='utf-8'))
    strict = verify_storage(SOURCE)
    (OUTPUT / 'factsheet-storage-verification.json').write_text(
        json.dumps(strict, indent=2, default=str) + '\n', encoding='utf-8')
    audit = {
        'generated_at': datetime.now(ZoneInfo('Asia/Kolkata')).isoformat(),
        'source_directory': str(SOURCE), 'read_only': True,
        'strict_verifier': strict, 'supplemental_checks': 0, 'material_mismatches': [],
        'provenance_differences': [], 'extra_stored_editions': [], 'schemes': [],
        'limitations': [
            'Compares database storage against the 28 available extracted records, not an independent re-extraction of every PDF field.',
            'Missing or unnormalized source values remain missing or raw; storing them does not make them normalized observations.',
            'PDF bytes are on disk; PostgreSQL stores their paths, checksums, metadata, and selected extracted page evidence.',
            'Online publication freshness was not rechecked. August and September 2026 reporting months are represented.',
            'The strict verifier and supplemental audit use separate read-only database transactions.',
        ],
    }

    def check(ok, name, field):
        audit['supplemental_checks'] += 1
        if not ok:
            audit['material_mismatches'].append({'fund': name, 'field': field})

    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        verify_target(conn)
        conn.row_factory = dict_row
        audit['database'] = conn.execute("SELECT current_database() AS name, current_schema() AS schema, "
            "transaction_timestamp() AS snapshot_at, current_setting('transaction_read_only') AS transaction_read_only").fetchone()
        audit['last_ingestion_runs'] = conn.execute("SELECT run_id,status,started_at,finished_at,inserted_count,"
            "updated_count,rejected_count,error_summary FROM sif_ingestion_runs WHERE source_code='OFFICIAL_FACTSHEET' "
            "ORDER BY run_id DESC LIMIT 5").fetchall()
        audit['unresolved_rejections'] = conn.execute("SELECT count(*) AS count FROM sif_ingestion_rejections "
            "WHERE source_code='OFFICIAL_FACTSHEET' AND fixed_at IS NULL").fetchone()['count']
        audit['table_counts'] = {}
        for table in ('sif_factsheet_documents', 'sif_factsheet_reports', 'sif_factsheet_pages') + TABLES:
            audit['table_counts'][table] = conn.execute(sql.SQL('SELECT count(*) AS count FROM public.{}')
                .format(sql.Identifier(table))).fetchone()['count']

        def records(table, report_id):
            return conn.execute(sql.SQL('SELECT * FROM public.{} WHERE report_id=%s')
                .format(sql.Identifier(table)), (report_id,)).fetchall()

        def compare(name, label, actual, expected):
            check(len(actual) == len(expected), name, label + '/row count')
            remaining = list(actual)
            for i, value in enumerate(expected, 1):
                match = next((j for j, row in enumerate(remaining)
                    if all(equivalent(row.get(key), val) for key, val in value.items())), None)
                check(match is not None, name, f'{label}/row {i} values')
                if match is not None:
                    remaining.pop(match)

        ids = []
        for path in sorted((SOURCE / 'extraction').glob('*.json')):
            for name, data in json.loads(path.read_text(encoding='utf-8')).items():
                found = conn.execute('SELECT r.*,d.sha256,d.local_storage_uri,d.official_only,d.official_pdf_url '
                    'FROM sif_factsheet_reports r JOIN sif_factsheet_documents d USING(document_id) '
                    'WHERE r.published_scheme_name=%s AND d.sha256=%s AND r.is_primary',
                    (name, data['Factsheet Source']['SHA256'])).fetchall()
                check(len(found) == 1, name, 'unique primary report for source checksum')
                if len(found) != 1:
                    continue
                report = found[0]
                rid = report['report_id']
                ids.append(rid)
                stored = report['raw_extraction_json']
                raw_keys = set(data) | set(stored)
                content_keys = raw_keys - {'Factsheet Source', 'Additional Factsheet Editions'}
                content_match = all(data.get(key) == stored.get(key) for key in content_keys)
                for key in content_keys:
                    check(data.get(key) == stored.get(key), name, 'raw content/' + key)
                local_source, db_source = data['Factsheet Source'], stored.get('Factsheet Source', {})
                source_keys = set(local_source) | set(db_source)
                for key in source_keys - VOLATILE_SOURCE_FIELDS:
                    check(local_source.get(key) == db_source.get(key), name, 'source identity/' + key)
                changed = {key: {'local': local_source.get(key), 'database': db_source.get(key)}
                    for key in source_keys if local_source.get(key) != db_source.get(key)}
                if changed:
                    audit['provenance_differences'].append({'scheme': name, 'fields': changed})
                local_editions = data.get('Additional Factsheet Editions') or []
                db_editions = stored.get('Additional Factsheet Editions') or []
                for edition in local_editions:
                    check(edition in db_editions, name, 'local additional edition retained')
                if len(db_editions) > len(local_editions):
                    audit['extra_stored_editions'].append({'scheme': name,
                        'local_count': len(local_editions), 'database_count': len(db_editions)})
                check(report['official_only'] is True, name, 'official document flag')
                check(report['official_pdf_url'] == local_source['PDF URL'], name, 'official document URL')
                val = data['Extraction Validation']
                for field, key in [('validation_checks', 'Checks'), ('validation_errors', 'Errors'),
                                   ('validation_warnings', 'Warnings'), ('extraction_adapter', 'Adapter')]:
                    check(report[field] == val.get(key), name, field)
                check(report['source_pages'] == (data.get('Source Pages') or []), name, 'source pages')
                check(report['portfolio_scope'] == data.get('Portfolio Scope', 'Published holdings'), name, 'portfolio scope')
                date = parse_portfolio_as_of(data['Portfolio Date'])
                profile = profile_values(data)
                numeric_profile = {'minimum_investment_inr', 'accredited_minimum_inr', 'portfolio_turnover_ratio'}
                profile = {key: val if key in numeric_profile or key == 'launch_date' else clean_text(val)
                           for key, val in profile.items()}
                profile['supplementary_fields'] = data['Fund Details']
                compare(name, 'profile', records('sif_report_strategy_profile', rid), [profile])
                aum = data.get('AUM (INR Cr)', {})
                net_aum = next((clean_numeric(aum[key]) for key in
                    ('Net AUM', 'Month End AUM', 'Published Fund Size') if aum.get(key) is not None), None)
                average_aum = clean_numeric(aum.get('Monthly Average AUM'))
                compare(name, 'AUM', records('sif_report_aum', rid), [{
                    'aum_as_of': date, 'net_aum_cr': net_aum,
                    'monthly_average_aum_cr': average_aum, 'supplementary_fields': aum,
                }] if net_aum is not None or average_aum is not None or aum else [])
                compare(name, 'allocations', records('sif_report_allocations', rid), [{
                    'allocation_family': value['family'], 'bucket_label': value['label'],
                    'exposure_percent': value['percent'], 'published_order': value['order'],
                    'source_page': value['page'], 'extra': value['extra'],
                } for value in allocation_rows(data)])
                compare(name, 'indicators', records('sif_report_statistical_indicators', rid), [{
                    'family': value['family'], 'indicator_name': value['name'], 'numeric_value': value['numeric'],
                    'text_value': value['text'], 'unit': value['unit'], 'attributes': value['attributes'],
                } for value in quant_rows(data)])
                compare(name, 'published performance', records('sif_report_published_performance', rid), [{
                    'performance_family': value['family'], 'published_period_label': value['period'],
                    'plan_label': value['plan'], 'option_label': value['option'], 'period_start': value['start'],
                    'period_end': value['end'], 'scheme_return_pct': value['scheme_return'],
                    'benchmark_return_pct': value['benchmark_return'], 'additional_values': value['extra'],
                } for value in performance_rows(data)])
                compare(name, 'IDCW', records('sif_report_idcw_history', rid), [{
                    'event_date': value['date'], 'plan_label': value['plan'],
                    'amount_per_unit_inr': value['amount'], 'attributes': value['attributes'],
                } for value in idcw_rows(data)])
                managers = conn.execute('SELECT m.manager_name,f.* FROM sif_report_fund_managers f '
                    'JOIN sif_fund_managers m USING(manager_id) WHERE f.report_id=%s', (rid,)).fetchall()
                compare(name, 'managers', managers, [{
                    'manager_name': m['Name'].strip(), 'manager_role': m.get('Role') or 'Fund Manager',
                    'experience_text': clean_text(m.get('Experience')),
                    'managing_since': parse_flexible_date(m.get('Managing Since')),
                    'published_footnote': clean_text(m.get('Published Footnote')),
                    'supplementary_fields': m.get('Supplementary Fields', {}),
                } for m in data.get('Fund Managers', []) if m.get('Name')])
                nav = []
                for label, value in data.get('NAV', {}).items():
                    number = clean_numeric(value)
                    if number is None or number <= 0:
                        continue
                    low = label.lower()
                    nav.append({'published_label': label, 'nav': number, 'nav_date': date,
                        'plan_type': 'DIRECT' if 'dir' in low else 'REGULAR' if 'reg' in low else None,
                        'option_type': 'GROWTH' if 'growth' in low else 'IDCW' if 'idcw' in low else None})
                compare(name, 'NAV', records('sif_report_nav', rid), nav)
                expenses = []
                for label, value in data.get('Base Expense Ratio (%)', {}).items():
                    number = clean_numeric(value)
                    if number is None or number < 0:
                        continue
                    low = label.lower()
                    expenses.append({'plan_type': 'DIRECT' if 'dir' in low else 'REGULAR' if 'reg' in low else 'OTHER',
                        'ratio_type': 'TER' if 'ter' in low else 'BASE', 'ratio_percent': number, 'as_of_date': date})
                compare(name, 'expense ratios', records('sif_report_expense_ratios', rid), expenses)
                risk = data.get('Risk Band & Strategy', {})
                compare(name, 'risk bands', records('sif_report_risk_bands', rid), [{
                    'investment_strategy_risk_band': clean_text(risk.get('Investment Strategy Risk Band') or risk.get('Riskometer')),
                    'benchmark_risk_band': clean_text(risk.get('Benchmark Risk Band')),
                    'benchmark_name': clean_text(risk.get('Benchmark')), 'attributes': risk,
                }] if risk else [])
                compare(name, 'notes', records('sif_report_notes', rid), [
                    {'note_family': 'PORTFOLIO', 'note_text': note.strip()}
                    for note in data.get('Published Portfolio Notes', []) if isinstance(note, str) and note.strip()])
                links = [link if isinstance(link, dict) else {'URL': link}
                         for link in data.get('External Field Links') or []]
                compare(name, 'links', records('sif_report_external_field_links', rid), [{
                    'published_field': link.get('Published Field'), 'target_field': link.get('Target Field'),
                    'source_name': link.get('Source Name'), 'external_url': link.get('URL') or link.get('url'),
                    'extra': link,
                } for link in links])
                ext = records('sif_report_extension_data', rid)
                for key in EXTENSIONS:
                    # Alternate editions may contain additional evidence retained by the previous online run.
                    value = stored.get(key) if key == 'Additional Factsheet Editions' else data.get(key)
                    actual = [row for row in ext if row['field_key'] == key]
                    compare(name, 'extension/' + key, actual, [
                        {'field_key': key, 'field_group': 'EXTRACTION', 'raw_value': value}] if value else [])
                for section, field in [('MAIN', 'Portfolio'), ('DERIVATIVE', 'Derivative Portfolio')]:
                    holdings = {row['row_number']: row for row in records('sif_report_holdings', rid)
                                if row['portfolio_section'] == section}
                    for i, holding in enumerate(data.get(field) or [], 1):
                        if not holding.get('Instrument'):
                            continue
                        row = holdings.get(i)
                        if row is None:
                            check(False, name, f'{section}/{i}/missing holding')
                            continue
                        for col, source_key in [('category_name', 'Category'), ('published_precision_marker', 'Published Precision Marker')]:
                            check(row[col] == clean_text(holding.get(source_key)), name, f'{section}/{i}/{col}')
                        check(row['is_aggregate'] == bool(holding.get('Aggregate', False)), name, f'{section}/{i}/aggregate')
                        check(row['supplementary_fields'] == holding.get('Supplementary Fields', {}), name, f'{section}/{i}/supplementary')
                audit['schemes'].append({'scheme': name, 'report_id': rid, 'document_id': report['document_id'],
                    'reporting_month': local_source['Reporting Month'], 'raw_published_content_matches': content_match,
                    'validation_status': report['validation_status'],
                    'main_holdings': len(data.get('Portfolio') or []),
                    'derivative_holdings': len(data.get('Derivative Portfolio') or []),
                    'counts': {table: len(records(table, rid)) for table in TABLES}})

        audit['audited_table_counts'] = {table: conn.execute(sql.SQL(
            'SELECT count(*) AS count FROM public.{} WHERE report_id=ANY(%s)').format(sql.Identifier(table)),
            (ids,)).fetchone()['count'] for table in TABLES}
        audit['field_availability'] = conn.execute('SELECT availability_status,count(*) AS fields '
            'FROM sif_report_field_availability WHERE report_id=ANY(%s) GROUP BY availability_status '
            'ORDER BY availability_status', (ids,)).fetchall()
        audit['normalization_gaps'] = conn.execute("SELECT field_path,count(*) AS fields "
            "FROM sif_report_field_availability WHERE report_id=ANY(%s) AND availability_status='PARSE_FAILED' "
            "GROUP BY field_path ORDER BY fields DESC,field_path", (ids,)).fetchall()
        audit['missing_source_schemes'] = []
        for row in coverage['Schemes']:
            if row['Status'] == 'extracted':
                continue
            count = conn.execute('SELECT count(*) AS count FROM sif_factsheet_reports '
                'WHERE published_scheme_name=%s', (row['Scheme'],)).fetchone()['count']
            audit['missing_source_schemes'].append({'scheme': row['Scheme'],
                'database_report_count': count, 'source_status': row['Status'], 'reason': row.get('Reason')})
        audit['document_checks'] = []
        for entry in manifest['Files']:
            docs = conn.execute('SELECT document_id,sha256,local_storage_uri FROM sif_factsheet_documents '
                'WHERE sha256=%s', (entry['SHA256'],)).fetchall()
            valid = len(docs) == 1 and Path(docs[0]['local_storage_uri']).is_file() and hashlib.sha256(
                Path(docs[0]['local_storage_uri']).read_bytes()).hexdigest() == entry['SHA256']
            check(valid, entry['File Name'], 'database document and PDF checksum')
            audit['document_checks'].append({'file': entry['File Name'], 'checksum_verified': valid})

    # Keep the strict failures visible; classify only the differences independently inspected above.
    explained = {'complete source JSON', 'Additional Factsheet Editions'}
    audit['unexplained_strict_mismatches'] = [m for m in strict['mismatches'] if m['field'] not in explained]
    audit['material_mismatches'].extend(audit['unexplained_strict_mismatches'])
    audit['storage_status'] = ('FAIL' if audit['material_mismatches'] else
        'PASS_WITH_PROVENANCE_DIFFERENCES' if strict['mismatches'] else 'PASS')
    audit['coverage'] = {
        'registered_schemes': coverage['Registered Schemes'], 'local_extractions': coverage['Extracted Schemes'],
        'matched_database_reports': len(audit['schemes']), 'verified_documents': sum(d['checksum_verified'] for d in audit['document_checks']),
        'raw_content_matches': sum(s['raw_published_content_matches'] for s in audit['schemes']),
        'validation_statuses': dict(Counter(s['validation_status'] for s in audit['schemes'])),
        'reporting_months': dict(Counter(s['reporting_month'] for s in audit['schemes'])),
    }
    evidence = OUTPUT / 'factsheet-database-audit.json'
    evidence.write_text(json.dumps(audit, indent=2, default=str) + '\n', encoding='utf-8')
    write_reports(audit)
    print(json.dumps({key: audit[key] for key in ['storage_status', 'coverage', 'audited_table_counts',
        'field_availability', 'supplemental_checks', 'material_mismatches', 'extra_stored_editions']}, indent=2, default=str))


def write_reports(audit):
    counts = audit['audited_table_counts']
    coverage = audit['coverage']
    total_checks = audit['strict_verifier']['checks'] + audit['supplemental_checks']
    verdict = (f"All {coverage['matched_database_reports']} available primary factsheet records are stored, and their published content matches "
               f"the current extraction. Registered strategy coverage: {coverage['matched_database_reports']} / {coverage['registered_schemes']}.")
    if audit['material_mismatches']:
        verdict = 'Storage verification found material mismatches; see the detailed JSON evidence.'
    paragraphs = [
        verdict,
        f"Inspected VB-SIF/public on {audit['generated_at']} using read-only PostgreSQL transactions. No database rows were changed.",
        f"Verified {coverage['matched_database_reports']} reports against {coverage['local_extractions']} local extractions and {coverage['verified_documents']} source PDFs. "
        f"Ran {total_checks:,} checks in the strict verifier and supplemental audit; material mismatches: {len(audit['material_mismatches'])}.",
        f"Published holdings: {sum(s['main_holdings'] for s in audit['schemes']):,} main portfolio rows plus "
        f"{sum(s['derivative_holdings'] for s in audit['schemes'])} derivative rows, for {counts['sif_report_holdings']:,} stored holdings.",
        f"The strict JSON/normalized-record verifier status is {audit['strict_verifier']['status']}, with "
        f"{len(audit['strict_verifier']['mismatches'])} exact-equality mismatches. "
        f"Provenance differences affect {len(audit['provenance_differences'])} reports; "
        f"{len(audit['extra_stored_editions'])} reports retain additional database-only alternate editions. "
        f"The storage assessment is {audit['storage_status']}. See JSON evidence for any differences.",
        f"Stored extraction validation: {coverage['validation_statuses']}. These are extraction warnings, separate from storage integrity.",
        f"Field availability counts: {dict((r['availability_status'], r['fields']) for r in audit['field_availability'])}. "
        'PARSE_FAILED includes values that were not normalized or could not be verified in the extraction; '
        'it does not establish that a value was published in the PDF. Raw JSON and extracted page evidence are retained. '
        'This audit therefore confirms storage of available extracted content, not complete structured extraction of every PDF field.',
        f"Reporting months: {coverage['reporting_months']}. No claim is made that all PDFs are the newest online edition.",
        f"Latest factsheet ingestion run: {audit['last_ingestion_runs'][0] if audit['last_ingestion_runs'] else 'none'}. "
        f"Unresolved factsheet ingestion rejections: {audit['unresolved_rejections']}.",
        'The comparison covers all 15 report child tables: profiles, managers, AUM, NAV, expense ratios, holdings, allocations, '
        'statistical indicators, risk bands, performance, IDCW, notes, links, availability and extension evidence. '
        'The strict verifier checks core normalized values, report identity/dates, PDF checksums and extracted page evidence; '
        'the supplemental audit adds raw content classification and checks omitted by the strict verifier.',
    ]
    md = ['# Factsheet database storage audit', '', *[p + '\n' for p in paragraphs], '## Stored rows in audited reports', '']
    md += [f'- `{table}`: {count:,}' for table, count in counts.items()]
    md += ['', '## Schemes without available source extractions', '']
    md += [f"- {row['scheme']}: {row['database_report_count']} database reports; {row['source_status']}." for row in audit['missing_source_schemes']]
    md += ['', '## Source-field normalization status', '']
    md += [f"- {row['availability_status']}: {row['fields']} fields." for row in audit['field_availability']]
    md += ['', 'Fields marked PARSE_FAILED:', '']
    md += [f"- {row['field_path']}: {row['fields']} entries." for row in audit['normalization_gaps']]
    md += ['', '## Scope and limitations', ''] + ['- ' + line for line in audit['limitations']]
    md += ['', '## Evidence', '', '- `factsheet-database-audit.json`: counts, per-scheme audit, provenance differences and limitations.',
           '- `factsheet-storage-verification.json`: unmodified strict verifier result, including any exact-equality mismatches.',
           '- Re-run: `.venv/Scripts/python.exe -m scripts.audit_factsheet_storage`.', '']
    (OUTPUT / 'factsheet-database-audit.md').write_text('\n'.join(md), encoding='utf-8')
    esc = lambda value: html.escape(str(value))
    def table(headers, rows):
        return '<table><thead><tr>' + ''.join('<th>' + esc(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(
            '<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table>'
    body = '<h1>Factsheet database storage audit</h1>' + ''.join('<p>' + esc(p) + '</p>' for p in paragraphs)
    body += '<h2>Stored rows in audited reports</h2>' + table(['Database table', 'Rows'], counts.items())
    body += '<h2>Per-scheme evidence</h2>' + table(['Scheme', 'Month', 'Report ID', 'Main holdings', 'Derivatives', 'Published content', 'Extraction validation'], [
        [s['scheme'], s['reporting_month'], s['report_id'], s['main_holdings'], s['derivative_holdings'],
         'MATCH' if s['raw_published_content_matches'] else 'MISMATCH', s['validation_status']] for s in audit['schemes']])
    body += '<h2>Unavailable source schemes</h2>' + table(['Scheme', 'Database reports', 'Source status'], [
        [s['scheme'], s['database_report_count'], s['source_status']] for s in audit['missing_source_schemes']])
    body += '<h2>Normalization status</h2>' + table(['Availability status', 'Fields'], [
        [s['availability_status'], s['fields']] for s in audit['field_availability']])
    body += '<h3>Fields marked PARSE_FAILED</h3>' + table(['Field', 'Entries'], [
        [s['field_path'], s['fields']] for s in audit['normalization_gaps']])
    body += '<h2>Scope and limitations</h2><ul>' + ''.join('<li>' + esc(p) + '</li>' for p in audit['limitations']) + '</ul>'
    body += '<p>Evidence: factsheet-database-audit.json and factsheet-storage-verification.json.</p>'
    document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    document += '<title>Factsheet database storage audit</title><style>body{font:15px/1.6 system-ui,sans-serif;max-width:1180px;margin:40px auto;padding:0 24px;color:#18212d;background:#fff}h1{font-size:28px}h2{font-size:20px;margin-top:36px}p{max-width:1000px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px 12px;border-bottom:1px solid #dce0e5;text-align:left}th{background:#f3f5f7}tbody tr:nth-child(even){background:#fafbfc}@media print{body{margin:0}tr{break-inside:avoid}}</style><body>'
    (OUTPUT / 'factsheet-database-audit.html').write_text(document + body + '</body></html>\n', encoding='utf-8')


if __name__ == '__main__':
    main()
