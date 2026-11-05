SIF data - Specialized Investment Funds, India
================================================================

STATUS: KEPT SEPARATE FOR NOW. This data runs on its own pipeline. It is not yet
part of the mutual fund data behind MFPro (https://www.tigzig.com/mfpro). We plan
to bring the two together once a few integration issues are worked out, and until
then it stays separate.

WHAT THIS IS
Every NAV that AMFI has published for Indian Specialized Investment Funds since
the category began, plus the scheme master and the quarterly average AUM. SIF is
a young category - the first NAV in this data is 2025-10-08 - and AMFI's main
mutual fund feed does not carry these schemes at all, which is why a separate
collection exists.

Everything here is at the SCHEME level, which is the level AMFI publishes at. A single
fund offering a Direct and a Regular plan, each with a Growth and an IDCW option,
appears as four separate schemes, each with its own code, NAV and AUM. Add the
variants up if you want the figure for the fund as a whole.

FILES
  sif_nav.csv     every NAV, every scheme, every date          15500 rows
  sif_meta.csv    the scheme master                            126 rows
  sif_aaum.csv    quarterly average AUM per scheme, in lakhs   295 rows
  summary.json    precomputed counts and series
  manifest.json   row counts, byte sizes and SHA-256 for each file

Each of the three data files also comes as .parquet - sif_nav.parquet,
sif_meta.parquet, sif_aaum.parquet. Same query, same rows, same column order, so
the two can never disagree on content. Parquet is much smaller and carries types.
The one difference: a missing value is a typed NULL in parquet and an empty string
in the CSV, because CSV has no way to say NULL. Take the CSV if you want to open
it in a spreadsheet; take the parquet for anything that will query it.

UPDATED
Twice daily, at about 05:05 and 11:45 IST. AAUM is quarterly data and is refreshed
weekly. Those times are ours and are not triggered by AMFI - AMCs file individually
through the evening and into the next morning, so a recent date can still gain rows
after we have fetched it. generated_at in manifest.json is the truth; do not infer
freshness from anything else.

THE temp_ PREFIX - READ THIS BEFORE YOU SCRIPT AGAINST A COLUMN
Any column named temp_something is TEMPORARY. It exists only while SIF is a
separate dataset, and it will NOT be there once SIF folds into the main mutual
fund scheme master. If you write code against a temp_ column, that code will
break on the day of the merge.

Everything without the prefix is intended to be permanent.

Today the temporary ones are:
  temp_sif_code    the display form, "SIF-120". Redundant once merged, because
                   scheme_code=120 plus source='SIF' reconstructs it exactly.
  temp_plan_basis  whether the plan was filed by AMFI or inferred by us. Real and
                   useful, but the mutual fund dataset has no equivalent column,
                   so it cannot survive a merge until that side gains one.
  temp_idcw_mode   payout / reinvestment / both. Same reason.
  temp_idcw_freq   monthly / quarterly / annual. Same reason.
  temp_n_nav_days  a count of NAV rows per scheme. A convenience we compute; the
                   mutual fund dataset does not carry it.

`source` is deliberately NOT temp_. It is the other half of the merge key - a
merged row is scheme_code=120 with source='SIF' - so it is a permanent column
that simply does not exist in the mutual fund file yet.

SAME COLUMN NAMES AS THE MUTUAL FUND DATASET
These files deliberately use the same column names, in the same order, as the
TIGZIG mutual fund scheme master at api.tigzig.com/mf/v1/download?format=latest.
If you already parse that file, you can point the same code at these and it will
work. SIF-only columns are APPENDED AFTER the shared ones, so a positional reader
is safe too. scheme_code is the numeric part of the SIF code, so it parses as an
integer exactly like an MF scheme code does, and `source` tells the two apart if
you stack them.

EVERY COLUMN, EXPLAINED

sif_meta.csv - one row per scheme. Same shape as the MF scheme master.
  scheme_code  the numeric part of the SIF code. SIF-120 is 120. Integer.
               Every MF scheme code is 100,027 or higher, so SIF and MF codes
               cannot collide if you stack the two datasets.
  isin         the scheme's PRIMARY ISIN. On an IDCW scheme this is the payout
               option's ISIN.
  isin2        the REINVESTMENT ISIN of the SAME scheme, where one exists.
               THIS IS THE ONE PEOPLE TRIP ON: isin and isin2 are two identifiers
               for ONE scheme, not two schemes. A statement showing the
               reinvestment ISIN is the same fund.
  scheme_name  full scheme name, as AMFI publishes it.
  amc          the fund house, as AMFI writes it. Lower case where they use lower
               case, e.g. "qsif SIF". Not tidied by us.
  scheme_type  "Open Ended Schemes" or "Interval Fund Schemes", as filed. Note
               that Interval is common in SIF and rare in mutual funds.
  category     AMFI's full category string, as filed, e.g.
               "Equity Oriented Investment Strategies - Equity Long-Short Fund".
  category_sub the SEBI investment strategy on its own, e.g.
               "Equity Long-Short Fund". DERIVED BY US by splitting `category`.
  category_group_clean  "Equity Scheme", "Debt Scheme" or "Hybrid Scheme", using
               the same words as the mutual fund dataset so the two stack.
               DERIVED BY US.
  category_group  the raw first half of `category`, as filed.
  scheme_plan  Direct or Regular. DERIVED from the scheme name - see plan_basis.
  scheme_option  Growth or IDCW. Derived from the scheme name.
  first_date   first date this scheme has a NAV here. Computed from the history.
  last_date    most recent date this scheme has a NAV. Computed.
  is_active    "true" if the scheme has a NAV within the last 45 days. Same rule
               and same lower-case spelling as the mutual fund dataset.
  is_stale     always "false" here. The column exists so the shape matches; SIF
               has no legacy junk to tag.
  txic_code    always blank. TIGZIG scheme codes have not been minted for SIF.
  aaum_cr      average AUM for the latest quarter, in Rs crore.
  aaum_quarter the quarter that figure is for, e.g. "June-2026" - the quarter's
               END month, matching the mutual fund dataset.
  aaum_quarter_end  that quarter's end date.
  nav_date     date of the latest NAV.
  nav          the latest NAV, as filed.

  SIF-only, appended after the shared columns. temp_ means it goes away on
  merge - see the note at the top:
  source       always "SIF". Tells the rows apart if you stack this with the
               mutual fund data. NOT temporary - it is half the merge key.
  temp_sif_code  the display form, "SIF-120". Use scheme_code to join.
  temp_plan_basis  "stated" means AMFI named the plan in the scheme name.
               "sibling_complement" means WE INFERRED IT: several fund houses
               label only the Direct variant and leave its Regular twin bare, so
               a lone unnamed scheme beside a lone stated Direct is the Regular.
               FILTER ON plan_basis = 'stated' IF YOU ONLY WANT FILED DATA.
  temp_idcw_mode   Payout, Reinvestment or Both, for IDCW schemes. Derived.
  temp_idcw_freq   Monthly, Quarterly, Half Yearly, Annual where the name says so.
  temp_n_nav_days  how many days this scheme has a NAV for. Computed. Useful for
               spotting a scheme that stopped reporting.

sif_nav.csv - every NAV. Same shape as the mutual fund history file.
  scheme_code, date, nav, scheme_name, isin   exactly as the MF history file.
  isin2, amc                                  as above.
  upload_ts    AMFI's own publication timestamp for that NAV. STORED EXACTLY AS
               FILED, which means it is IST despite the trailing Z in the source.
               Anything computing publication lag from it is 5h30m out unless it
               converts. See the anomalies section on the site.
  temp_sif_code  the display form.

sif_aaum.csv - quarterly average AUM, one row per scheme per quarter.
  scheme_code, aaum_quarter, aaum_quarter_end, aaum_cr   as in sif_meta.csv.
  aaum_lakhs   the figure exactly as AMFI publishes it, in Rs lakhs. aaum_cr is this
               over 100. AMFI's own field is named "ForTheMonth" and that is a
               misnomer - their own page heading says quarter.
  scheme_name, amc, quarter_start, temp_sif_code

WHICH COLUMNS ARE AMFI'S AND WHICH ARE OURS
As filed by AMFI: scheme_code (as SIF-n), isin, isin2, scheme_name, amc,
scheme_type, category, date, nav, upload_ts, aaum_lakhs, quarter dates.
Computed by us: category_sub, category_group_clean, category_group, scheme_plan,
scheme_option, first_date, last_date, is_active, is_stale, aaum_cr, aaum_quarter,
source, plan_basis, idcw_mode, idcw_freq, n_nav_days.

SOURCE
AMFI (Association of Mutual Funds in India). This is a third-party copy and is not
affiliated with or endorsed by AMFI. Refer to amfiindia.com for the official data.

CONTACT
Amar Harolikar - amar@harolikar.com
