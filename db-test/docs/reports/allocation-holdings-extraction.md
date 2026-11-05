# Allocation and holdings extraction

Completed 19 October 2026 using the locally downloaded official PDF editions.

## Result

- Reprocessed 16 PDFs into 28 individual scheme JSON files.
- Sector allocation is published and extracted for 23 schemes; five source reports have no sector split.
- Asset allocation is extracted for 24 schemes, including explicitly published partial category totals; four source reports have no asset split.
- Rating allocations are extracted for seven schemes. Market-cap allocations remain available for 16 schemes with no numerical changes.
- Structured holdings: 1,732 cash/portfolio records and nine derivative records, including published aggregate rows.
- Six registered strategies have no locally available source report and therefore no extracted JSON.
- All four allocation families have zero PARSE_FAILED statuses in these outputs. Independent availability gaps in other factsheet fields remain explicitly flagged.

## Corrections

Chart-only labels were previously retained as page evidence without allocation dictionaries.
The shared allocation enrichment now reads printed table/chart labels on each scheme's
attributed source pages. It preserves published signs and denominators, keeps index
comparisons and cash/derivative exposures separate, and retains chart evidence in
Supplementary Fields / Published Allocation Groups.

Sapphire now contains all 35 industry-chart rows, including Banks 17.47% and
Agricultural, Commercial & Construction Vehicles 1.39%. Wrapped holdings headings
no longer assign Tata Motors to the preceding Aerospace & Defense sector.

WSIF now contains the two published futures positions and the cash aggregate in
addition to its ten published equity holdings. Validation counts the appropriate
equity or mixed-asset top-ten list rather than rejecting valid additional cash rows.

The five quant comparative charts use visually verified printed values keyed by
exact embedded-image SHA256 because local OCR could not reliably read their tiny
rotated labels. Zero and negative weights are preserved. Changed images require
review and produce PARSE_FAILED; values are never estimated from bar heights.
Quant market-cap and asset pie labels continue to use local OCR.

Previously verified alternate plan editions are retained when re-extracting the
same primary PDF. The two DynaSIF alternate editions were preserved in this run.

## Validation and storage

- 35 extraction, normalization and ingestion regression tests passed.
- Source PDF and per-scheme JSON checksums, ZIP integrity, all unchanged market-cap values, alternate-edition evidence and unaffected fields were checked.
- PostgreSQL ingestion run 39 succeeded: 16 documents, 28 reports, 1,741 holdings and 806 allocation records; no rejected reports.
- Strict and supplemental database verification passed with zero material mismatches; all 28 source JSON records match their stored content.
- Scope remains as published: top-ten holdings stay marked as top-ten; no missing positions or allocation remainder are inferred.
- Source editions are September 2026 for 25 schemes and August 2026 for three schemes. This offline run did not check online freshness.

## Deliverables

- data/factsheets/latest/extraction/: one JSON file per available scheme.
- data/factsheets/latest/extracted_factsheets.json: combined records.
- data/factsheets/latest/allocation-holdings-coverage.json: per-scheme availability and counts.
- data/factsheets/latest/scheme-allocations-and-holdings.zip: individual JSON files plus coverage and checksums.

See the README's Factsheet allocation and holdings JSON section for field semantics
and the repeatable extraction/export commands.
