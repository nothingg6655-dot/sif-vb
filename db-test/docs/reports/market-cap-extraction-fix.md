# Market-cap extraction correction

Completed 2026-10-19T12:29:56.458173+05:30.

## Cause

- Most AMC adapters left the market-cap field empty; only DynaSIF and one iSIF main-page layout implemented it.
- iSIF Ex-Top 100 publishes the split on a continuation page.
- Quant publishes the chart as an embedded raster image, so PDF text extraction cannot see its values.
- Altiva and SBI donut numbers require matching the chart slice to the legend rather than relying on PDF text order.
- The old availability flag classified missing normalization as PARSE_FAILED without distinguishing source absence.

## Correction

- A shared enrichment step scans the pages attributed to each scheme and reads the supported published tables and charts.
- Quant chart labels use local OCR, with word coordinates and image checksums retained as evidence.
- Gross/net series remain separate. Published allocation percentages retain their denominators; no totals are rescaled.
- Extraction code and OCR bridge changes invalidate the monitor cache.
- Previously stored alternate-edition evidence was preserved before loading the corrected extractions.

## Result

- Available market-cap strategies increased from 4 to 16; normalized allocation rows increased from 15 to 54.
- 12 inspected factsheets have no published market-cap split. Six registered strategies have no available factsheet report.
- Market-cap PARSE_FAILED count is zero. Other factsheet fields may still have independent normalization gaps.
- All 28 local factsheet extractions were reprocessed; PostgreSQL ingestion run 21 succeeded with no rejected reports.
- The source PDFs and published values were checked, including visual review of all five Quant charts.
- Nineteen extraction/normalization regression tests passed, including actual table, donut and OCR chart examples.
- Strict storage verification passed 29,093 checks with no mismatches; supplemental audit passed 9,934 checks.
- No online source freshness check was performed. These are the current downloaded August/September 2026 factsheets.

## Re-run

- Extract: `.venv/Scripts/python.exe -m factsheetDataIngestion.monitor --once --offline --no-browser --config data/factsheets/local_sources.json --output-dir data/factsheets`
- Load: `.venv/Scripts/python.exe -m factsheetDataIngestion load --dir data/factsheets/latest`
- Audit: `.venv/Scripts/python.exe -m scripts.audit_factsheet_storage`
- Export: `.venv/Scripts/python.exe -m scripts.export_market_cap`
