# SIF360 performance methodology reconciliation

## Evidence inspected

The calculation was inferred from the supplied 34-fund response for:

```text
to_date=2026-10-14
from1m=2026-09-14
from3m=2026-07-16
```

The reference response is retained as
`backend/tests/fixtures/sif360-performance-2026-10-14.json`. The available NAV
export is `data/tigzig/sif_nav.csv`, SHA-256
`7c715ec1fe87c0c8c8dfe77ce06fd9c957c62fe739d2e20669aa226a689e11b4`.
It has 15,620 rows, 126 scheme codes, no duplicate `(scheme_code,date)` keys,
no malformed identifiers or dates, and four non-positive NAV rows. Those four
rows are rejected by ingestion, leaving 15,616 valid database observations.

The full verified ID-to-scheme mapping is in
`docs/reports/sif360-performance-mapping.csv`. Every reference ID maps to one
Regular Growth scheme through canonical fund identity, stable source scheme code,
and ISIN. There are no ambiguous or unresolved mappings.

## Observed compatibility rules

The production method is identified as `sif360-observed-30d-90d-v1`.

- Use the configured Regular Growth scheme for each reference fund ID.
- `latestNav` is the last valid positive NAV on or before `to_date`.
- `latestDate` is that NAV observation date.
- `inceptionDate` is the first valid NAV observation date. It agrees with the
  source scheme's declared first NAV date for all 34 mappings.
- `changePct` is `((latestNav / inceptionNav) - 1) * 100`.
- The one-month target is 30 calendar days before `to_date`, or the supplied
  `from1m`. Select the first NAV on or after that target.
- The three-month target is 90 calendar days before `to_date`, or the supplied
  `from3m`. Select the first NAV on or after that target.
- If a fund began after a target, use its inception observation and mark the
  normalized calculation as partial.
- Preserve calculation precision through division, then round percentage returns
  to two decimal places with half-up rounding.
- The supplied response represents an exactly zero rounded return as `null`.
  The normalized result records a warning so this compatibility behavior is not
  confused with missing data.

Selecting the next observation is an observed compatibility behavior and can be
forward-looking relative to the target boundary. Reference selection is therefore
an explicit policy, separate from percentage calculation. Other uses can select
`on-or-before` without changing the arithmetic engine.

## Candidate results

| Metric | Candidate | Exact funds |
|---|---|---:|
| 1M | 30/calendar days, latest NAV on or before 14 Sep | 1/34 |
| 1M | 30/calendar days, first NAV on or after 14 Sep | 32/34 |
| 1M | 30/calendar days, first NAV strictly after 14 Sep | 32/34 |
| 1M | Best fixed observation interval, 19 | 31/34 |
| 3M | Three calendar months, first NAV on or after 14 Jul | 7/34 |
| 3M | 90 days, NAV on 16 Jul | 32/34 |
| 3M | 90 days, first NAV strictly after 16 Jul | 7/34 |
| 3M | Best fixed observation interval, 61 | 30/34 |

The maximum possible match rate from the supplied CSV is 32/34 because reference
IDs 12 and 25 have a 13 October NAV in the reference response while every variant
of those funds ends on 12 October in this source snapshot.

There is no NAV on 14 September, so this fixture cannot distinguish inclusive
`on-or-after` from `strictly-after` for 1M. There is an observation exactly on
16 July, so this fixture cannot distinguish `on-or-before` from `on-or-after` for
3M. The implementation uses inclusive `on-or-after` as the explicit configurable
policy and does not claim stronger certainty.

## Fund 1 regression calculation

For reference ID 1, scheme code 13:

| Metric | Target | Selected date | Selected NAV | Latest NAV | Unrounded | Output |
|---|---|---|---:|---:|---:|---:|
| Since inception | 29 Oct 2025 | 29 Oct 2025 | 10.0336 | 10.3021 | 2.676008611066% | 2.68 |
| 1M | 14 Sep 2026 | 15 Sep 2026 | 10.3919 | 10.3021 | -0.864134566344% | -0.86 |
| 3M | 16 Jul 2026 | 16 Jul 2026 | 10.4461 | 10.3021 | -1.378504896564% | -1.38 |

## Reconciliation outcome

The detailed 204-row table is
`docs/reports/sif360-performance-reconciliation.csv`; the machine-readable report,
normalized diagnostics, candidate comparison, regression evidence, and sample API
response are in `docs/reports/sif360-performance-reconciliation.json`.

- 192 non-null metrics match exactly.
- Two null metrics match exactly.
- Ten metrics mismatch, all on reference IDs 12 and 25.
- The other 32 funds match all six public fields exactly.

ID 12 maps to scheme 87 and ID 25 maps to scheme 124. Their inception dates match,
but the local source lacks the final 13 October observation, causing five mismatched
fields per fund. Reference values are never inserted into raw NAV history. Resolving
these cases requires a verified source update or a separately audited AMFI ingestion
path.

## API example

```json
{
  "success": true,
  "isBadData": false,
  "performanceMap": {
    "1": {
      "changePct": 2.68,
      "return1m": -0.86,
      "return3m": -1.38,
      "latestNav": 10.3021,
      "latestDate": "13 Oct 2026",
      "inceptionDate": "29 Oct 2025"
    }
  }
}
```

The internal normalized result additionally contains `schemeId`, both selected
reference NAVs and dates, `calculationStatus`, `calculationMethod`, and
`dataQualityWarnings`. These diagnostics are deliberately omitted from the public
compatibility payload.
