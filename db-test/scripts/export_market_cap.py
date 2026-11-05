"""Export the latest stored market-cap allocation and source status for every strategy."""
from collections import Counter
from decimal import Decimal
import html
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from factsheetDataIngestion.config import DatabaseConfig

OUTPUT = Path(__file__).resolve().parents[1] / 'docs/reports'


def encode(value):
    return float(value) if isinstance(value, Decimal) else value.isoformat()


def main():
    with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs(), row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        snapshot = conn.execute('SELECT current_database() AS database, transaction_timestamp() AS fetched_at').fetchone()
        schemes = conn.execute('''
            SELECT s.strategy_id,s.strategy_name,a.amc_name,r.report_id,r.report_month,r.portfolio_as_of,
                   d.official_pdf_url,d.sha256,f.availability_status,
                   r.raw_extraction_json->'Supplementary Fields'->'Market Capitalization Evidence' AS extraction_evidence
            FROM sif_strategies s JOIN sif_amcs a USING(amc_id)
            LEFT JOIN LATERAL (
                SELECT * FROM sif_factsheet_reports r WHERE r.strategy_id=s.strategy_id AND r.is_primary
                ORDER BY report_month DESC,extracted_at DESC,report_id DESC LIMIT 1
            ) r ON TRUE
            LEFT JOIN sif_factsheet_documents d USING(document_id)
            LEFT JOIN sif_report_field_availability f ON f.report_id=r.report_id
                AND f.field_path='Market Capitalization (%)'
            WHERE s.is_active ORDER BY lower(s.strategy_name)
        ''').fetchall()
        for scheme in schemes:
            scheme['market_cap_allocations'] = conn.execute('''
                SELECT bucket_label,exposure_percent,extra,source_page FROM sif_report_allocations
                WHERE report_id=%s AND allocation_family='MARKET_CAP' ORDER BY published_order,allocation_id
            ''', (scheme['report_id'],)).fetchall() if scheme['report_id'] else []
            scheme['status'] = ('AVAILABLE' if scheme['market_cap_allocations'] else
                'NO_FACTSHEET_REPORT' if scheme['report_id'] is None else scheme['availability_status'] or 'NOT_EXTRACTED')
            scheme['linked_schemes'] = conn.execute('''
                SELECT s.scheme_code,s.scheme_name,s.scheme_plan,s.scheme_option FROM sif_strategy_scheme_links l
                JOIN sif_schemes s USING(scheme_code) WHERE l.strategy_id=%s AND l.review_status='VERIFIED'
                ORDER BY s.scheme_code
            ''', (scheme['strategy_id'],)).fetchall()
    summary = dict(Counter(s['status'] for s in schemes))
    report = {**snapshot, 'scheme_count': len(schemes), 'summary': summary,
        'units': 'Published allocation percentage, not monetary market capitalization.',
        'selection': 'Latest primary factsheet per active strategy; Direct/Regular variants share the portfolio.',
        'notes': ['Gross and net are separate measures; an unspecified basis is not assumed to be either.',
            'Published percentages retain their original denominator and are not rescaled to 100%.',
            'NOT_PUBLISHED means no market-cap split was found in the inspected scheme pages of this source edition.',
            'Blank values are unavailable, not zero. No new online freshness check was performed.'],
        'schemes': schemes}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / 'all-schemes-market-cap.json').write_text(json.dumps(report, indent=2, default=encode) + '\n', encoding='utf-8')
    rows = []
    for scheme in schemes:
        groups = {}
        for allocation in scheme['market_cap_allocations']:
            extra = allocation['extra'] or {}
            groups.setdefault(extra.get('basis') or 'Published; basis unspecified', {})[
                extra.get('bucket_label') or allocation['bucket_label']] = allocation['exposure_percent']
        for basis, values in (groups or {'Unavailable': {}}).items():
            rows.append([scheme['strategy_name'], scheme['report_month'].strftime('%Y-%m') if scheme['report_month'] else '',
                basis, *[f'{values[bucket]:.2f}' if values.get(bucket) is not None else ''
                         for bucket in ('Large Cap', 'Mid Cap', 'Small Cap')], scheme['status']])
    esc = lambda value: html.escape(str(value))
    body = '<h1>Market-cap allocation: all schemes</h1><p>Source: ' + esc(snapshot['database']) + ' | Fetched ' + esc(snapshot['fetched_at']) + '</p>'
    body += '<p><strong>' + esc(summary.get('AVAILABLE', 0)) + ' of ' + str(len(schemes)) + ' schemes have published market-cap allocations.</strong> '
    body += '; '.join(esc(key) + ': ' + str(count) for key, count in summary.items()) + '.</p>'
    body += '<p>All values are percentages. Gross and net figures are shown separately. Blank cells mean unavailable.</p>'
    body += '<label>Filter schemes <input id="filter" type="search" placeholder="Scheme name, month or status"></label><div style="overflow-x:auto"><table><thead><tr>'
    body += ''.join('<th>' + esc(h) + '</th>' for h in ['Scheme', 'Month', 'Basis', 'Large cap (%)', 'Mid cap (%)', 'Small cap (%)', 'Status'])
    body += '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + esc(value) + '</td>' for value in row) + '</tr>' for row in rows)
    body += '</tbody></table></div><h2>Interpretation</h2><ul>' + ''.join('<li>' + esc(note) + '</li>' for note in report['notes']) + '</ul>'
    body += '<p>The JSON export includes source PDF URLs, pages, extraction methods and OCR evidence.</p>'
    body += "<script>document.getElementById('filter').addEventListener('input',function(){const q=this.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(row=>row.hidden=!row.textContent.toLowerCase().includes(q))});</script>"
    document = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>All schemes market-cap allocations</title>'
    document += '<style>body{font:15px/1.6 system-ui,sans-serif;color:#18212d;background:#fff;max-width:1260px;margin:36px auto;padding:0 24px}h1{font-size:26px}h2{font-size:20px}table{width:100%;border-collapse:collapse;margin-top:18px;font-size:13px}th,td{text-align:left;border-bottom:1px solid #dce0e5;padding:9px 12px}th{background:#f3f5f7;white-space:nowrap}td:nth-child(4),td:nth-child(5),td:nth-child(6){text-align:right;font-variant-numeric:tabular-nums}input{font:inherit;padding:6px 10px;margin-left:8px;border:1px solid #b7bdc6;border-radius:4px;min-width:290px}[hidden]{display:none}</style><body>'
    (OUTPUT / 'all-schemes-market-cap.html').write_text(document + body + '</body></html>\n', encoding='utf-8')
    print(json.dumps({'summary': summary, 'scheme_count': len(schemes),
        'market_cap_allocation_rows': sum(len(s['market_cap_allocations']) for s in schemes)}, indent=2))


if __name__ == '__main__':
    main()
