# Milestone 7 — REST APIs and performance calculations

Implemented all six approved endpoints with parameter validation, stable error
serialization, bounded NAV-history pagination, canonical fund resolution, numeric
serialization, and explicit source limitations. Responses retain the supplied
`data`, `monthlyMap`, and `performanceMap` styles.

The return service uses last-known observations on or before requested dates and the
absolute return formula. Inception fallbacks and incomplete months are marked partial.
Daily change, one-month, three-month, and since-inception values are separate fields;
no absolute value is labelled annualized.

The ingestion transaction creates a temporary distinct set of source schemes whose
NAV rows are new or changed. Only those schemes have `app_monthly_returns` and
`app_performance_cache` replaced, in the same transaction as NAV changes and source
checkpoints. Rollback therefore also restores prior analytics. The initial refresh
produced 882 monthly rows and 126 performance rows.

The benchmark provider is an interface with an unavailable response because TIGZIG
has no complete benchmark history. Factsheet and TER fields are similarly unavailable;
no external or expense values are invented.

Administrative status access is allowed without credentials only on loopback. A
non-loopback bind is rejected unless `ADMIN_API_KEY` is configured, after which the
route requires a matching bearer token.

Automated verification covers all API success shapes, invalid parameters, pagination,
not-found responses, numeric JSON output, partial-period calculations, month-end
clamping, benchmark/TER placeholders, admin authentication, affected-scheme cache
refresh, ingestion rollback, TypeScript compilation, and prior milestone regressions.
