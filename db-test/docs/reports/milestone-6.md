# Milestone 6 — canonical fund mapping and read models

Migrations `0002_canonical_fund_read_models.sql` and
`0003_ticker_order_deferrable.sql` add mapping review, ticker selection, monthly
return, performance cache, and external identifier tables without changing provider
history or creating factsheet tables. Display order uniqueness is transaction-deferred
so future source additions can reorder ticker rows atomically.

The `amc-category-isin-v1` matcher accepts a mapping only when the scheme has an exact
fund house, a provider `category_sub` family, a supported Direct/Regular and
Growth/IDCW variant, and at least one source ISIN. Punctuation/case normalization is
used only to detect equal or conflicting exact provider labels. Fuzzy similarity
never writes a verified mapping. Missing and conflicting candidates are persisted
for review.

Executed reconciliation results:

- 126 source schemes inspected and mapped.
- 34 deterministic canonical funds created as active.
- 175 verified ISIN identifiers stored with scheme provenance.
- 34 verified ticker selections created; every selection is Direct Growth.
- 0 pending ambiguous mappings for the current snapshot.
- 0 monthly return or performance cache rows, as calculations begin in Milestone 7.

Tests prove exact-key grouping, ambiguity quarantine, complete verified identifiers,
ticker-to-mapping foreign-key enforcement, globally unique identifiers, idempotent
reconciliation, and name normalization behavior.
