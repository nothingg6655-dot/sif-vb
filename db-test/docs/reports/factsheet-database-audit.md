# Factsheet database storage audit

All 28 available primary factsheet records are stored, and their published content matches the current extraction. Registered strategy coverage: 28 / 34.

Inspected VB-SIF/public on 2026-10-19T16:20:53.818302+05:30 using read-only PostgreSQL transactions. No database rows were changed.

Verified 28 reports against 28 local extractions and 16 source PDFs. Ran 40,029 checks in the strict verifier and supplemental audit; material mismatches: 0.

Published holdings: 1,732 main portfolio rows plus 9 derivative rows, for 1,741 stored holdings.

The strict JSON/normalized-record verifier status is PASSED, with 0 exact-equality mismatches. Provenance differences affect 0 reports; 0 reports retain additional database-only alternate editions. The storage assessment is PASS. See JSON evidence for any differences.

Stored extraction validation: {'PASSED': 13, 'WARNING': 15}. These are extraction warnings, separate from storage integrity.

Field availability counts: {'NOT_PUBLISHED': 42, 'PARSE_FAILED': 19, 'PUBLISHED': 147}. PARSE_FAILED includes values that were not normalized or could not be verified in the extraction; it does not establish that a value was published in the PDF. Raw JSON and extracted page evidence are retained. This audit therefore confirms storage of available extracted content, not complete structured extraction of every PDF field.

Reporting months: {'2026-09': 25, '2026-08': 3}. No claim is made that all PDFs are the newest online edition.

Latest factsheet ingestion run: {'run_id': 40, 'status': 'SUCCEEDED', 'started_at': datetime.datetime(2026, 10, 19, 16, 20, 40, 746157, tzinfo=zoneinfo.ZoneInfo(key='Asia/Kolkata')), 'finished_at': datetime.datetime(2026, 10, 19, 16, 20, 40, 746157, tzinfo=zoneinfo.ZoneInfo(key='Asia/Kolkata')), 'inserted_count': 0, 'updated_count': 28, 'rejected_count': 0, 'error_summary': None}. Unresolved factsheet ingestion rejections: 0.

The comparison covers all 15 report child tables: profiles, managers, AUM, NAV, expense ratios, holdings, allocations, statistical indicators, risk bands, performance, IDCW, notes, links, availability and extension evidence. The strict verifier checks core normalized values, report identity/dates, PDF checksums and extracted page evidence; the supplemental audit adds raw content classification and checks omitted by the strict verifier.

## Stored rows in audited reports

- `sif_report_strategy_profile`: 28
- `sif_report_fund_managers`: 89
- `sif_report_aum`: 28
- `sif_report_nav`: 71
- `sif_report_expense_ratios`: 42
- `sif_report_holdings`: 1,741
- `sif_report_allocations`: 806
- `sif_report_statistical_indicators`: 14
- `sif_report_risk_bands`: 3
- `sif_report_published_performance`: 38
- `sif_report_idcw_history`: 16
- `sif_report_notes`: 1
- `sif_report_external_field_links`: 11
- `sif_report_field_availability`: 208
- `sif_report_extension_data`: 94

## Schemes without available source extractions

- Apex Hybrid Long-Short Fund: 0 database reports; unavailable.
- Arthaya Equity Long-Short Fund: 0 database reports; unavailable.
- Magnum Equity Ex-Top 100 Long-Short Fund: 0 database reports; unavailable.
- Apex Equity Long-Short Fund: 0 database reports; unavailable.
- Apex Equity Ex-Top 100 Long-Short Fund: 0 database reports; unavailable.
- Altiva Equity Long-Short Fund: 0 database reports; unavailable.

## Source-field normalization status

- NOT_PUBLISHED: 42 fields.
- PARSE_FAILED: 19 fields.
- PUBLISHED: 147 fields.

Fields marked PARSE_FAILED:

- Base Expense Ratio (%): 7 entries.
- Investment Objective: 7 entries.
- NAV: 5 entries.

## Scope and limitations

- Compares database storage against the 28 available extracted records, not an independent re-extraction of every PDF field.
- Missing or unnormalized source values remain missing or raw; storing them does not make them normalized observations.
- PDF bytes are on disk; PostgreSQL stores their paths, checksums, metadata, and selected extracted page evidence.
- Online publication freshness was not rechecked. August and September 2026 reporting months are represented.
- The strict verifier and supplemental audit use separate read-only database transactions.

## Evidence

- `factsheet-database-audit.json`: counts, per-scheme audit, provenance differences and limitations.
- `factsheet-storage-verification.json`: unmodified strict verifier result, including any exact-equality mismatches.
- Re-run: `.venv/Scripts/python.exe -m scripts.audit_factsheet_storage`.
