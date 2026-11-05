# TIGZIG source inspection for schema design

Inspected on 13 October 2026 (Asia/Kolkata). Source documentation:
[README](https://sif.tigzig.com/README.txt) and
[manifest](https://sif.tigzig.com/manifest.json). Local copies are in `docs/sources/`.
This is an inspection, not a Milestone 2 download/validation implementation.

The actual manifest is an object with `generated_at`, `files` as an **array**,
coverage, status, source attribution, and a documentation link. File entries contain
`name`, `rows`, `columns` (array), `bytes`, and `sha256`; they are not nested under
assumed dataset keys. The inspected generation timestamp is
`2026-10-13T06:16:27+00:00` (11:46:27 IST).

The inspected Parquet entries report 126 metadata rows (11,468 bytes), 15,500 NAV
rows (96,199 bytes), and 295 AAUM rows (8,936 bytes). These are an observation,
not hardcoded import expectations. README's example counts lag the current manifest.
No Parquet files have been downloaded, parsed, or imported in Milestone 1.

The metadata entry includes the permanent `source` column. NAV and AAUM entries
do not list `source`, so the later importer must resolve their source from the
dataset/metadata context; it cannot assume it exists in every row. Scheme identity
is `(source, scheme_code)`, with metadata's current source value documented as `SIF`.

Permanent metadata fields map directly to scheme columns, with aliases:
`amc` to `fund_house_id` via preserved `original_amc_name`, `first_date`/`last_date`
to `first_nav_date`/`last_nav_date`, `nav`/`nav_date` to latest NAV fields, and AAUM
fields to latest AAUM snapshot fields. `isin` and `isin2` populate PRIMARY and
SECONDARY identifiers on one scheme. All documented `temp_` fields are nullable
and supplementary; `source_metadata` can preserve newly introduced optional fields.

NAV's `date` maps to `nav_date` and `upload_ts` maps to `upload_ts_raw`. Source
documentation states that AMFI publication timestamps are IST wall-clock values
even when the text carries `Z`. Retaining raw text prevents accidental UTC parsing.
AAUM's `aaum_quarter_end` maps to `quarter_end`; lakhs are the published unit and
crores are lakhs divided by 100.

The provider publishes snapshots. Later synchronization must compare full source
business keys and write changed/new rows without discarding historical data.
No factsheet, benchmark, expense-ratio, matching, or calculation data has been added.
