"""Stable JSON schema shared by every extracted SIF scheme."""
from copy import deepcopy


# Keep all published fields that existing consumers may use, but emit them in
# one order with one default type for every scheme.
FUND_FIELD_DEFAULTS = (
    ('AMC / Fund House', None),
    ('Portfolio Date', None),
    ('Investment Objective', ''),
    ('Fund Managers', []),
    ('Fund Details', {}),
    ('AUM (INR Cr)', {}),
    ('NAV', {}),
    ('Base Expense Ratio (%)', {}),
    ('Portfolio', []),
    ('Portfolio Totals (%)', {}),
    ('Sector Allocation (%)', {}),
    ('Market Capitalization (%)', {}),
    ('Asset Allocation (%)', {}),
    ('Rating Profile (%)', {}),
    ('Statistical Debt Indicators', {}),
    ('Risk Band & Strategy', {}),
    ('Portfolio Summary (%)', {}),
    ('Debt Portfolio Quants', {}),
    ('Monthly Rolling Performance', []),
    ('Scheme Performance', []),
    ('IDCW History', {}),
    ('Published Pages', []),
    ('Source Pages', []),
    ('Portfolio Scope', ''),
    ('Published Portfolio Rows', []),
    ('Published Portfolio Aggregates (%)', {}),
    ('Published Portfolio Quants', []),
    ('Published Portfolio Notes', []),
    ('Published Asset Allocation Rows', []),
    ('Published Derivative and Cash Rows', []),
    ('Published Debt Category Rows', []),
    ('Published Other Exposure Rows', []),
    ('Shared Annexure Pages', []),
    ('Published AUM Corrections', {}),
    ('External Field Links', []),
    ('Derivative Portfolio', []),
    ('Derivative Totals (%)', {}),
    ('Field Availability', {}),
    ('Additional Factsheet Editions', []),
    ('Extraction Validation', {}),
    ('Factsheet Source', {}),
    ('Supplementary Fields', {}),
)
CANONICAL_FUND_FIELDS = tuple(name for name, _ in FUND_FIELD_DEFAULTS)

MANAGER_FIELD_DEFAULTS = (
    ('Name', ''),
    ('Role', ''),
    ('Experience', None),
    ('Managing Since', None),
    ('Published Footnote', None),
    ('Supplementary Fields', {}),
)
CANONICAL_MANAGER_FIELDS = tuple(name for name, _ in MANAGER_FIELD_DEFAULTS)

PORTFOLIO_FIELD_DEFAULTS = (
    ('Instrument', ''),
    ('Category', ''),
    ('Sector', None),
    ('Percent to Net Assets', None),
    ('Aggregate', False),
    ('Source Page', None),
    ('Market Capitalization', None),
    ('Rating', None),
    ('Rating / Classification', None),
    ('Quantity', None),
    ('Market Value (INR Lakhs)', None),
    ('Derivatives Percent to Net Assets', None),
    ('Hedged Percent to Net Assets', None),
    ('Unhedged Percent to Net Assets', None),
    ('Published Net Exposure (%)', None),
    ('Published Precision Marker', None),
    ('Derivative Type', None),
    ('Asset Class', None),
    ('Supplementary Fields', {}),
)
CANONICAL_PORTFOLIO_FIELDS = tuple(name for name, _ in PORTFOLIO_FIELD_DEFAULTS)


def _ordered_record(record, defaults):
    known={name for name, _ in defaults}
    result={name:deepcopy(record.get(name, default)) for name, default in defaults}
    extra={key:value for key,value in record.items() if key not in known}
    if extra:
        result['Supplementary Fields'].update(extra)
    return result


def normalize_fund_record(data):
    """Return a record with stable top-level, manager, and portfolio shapes."""
    result=_ordered_record(data,FUND_FIELD_DEFAULTS)
    result['Fund Managers']=[_ordered_record(row,MANAGER_FIELD_DEFAULTS) for row in result['Fund Managers']]
    result['Portfolio']=[_ordered_record(row,PORTFOLIO_FIELD_DEFAULTS) for row in result['Portfolio']]
    result['Derivative Portfolio']=[_ordered_record(row,PORTFOLIO_FIELD_DEFAULTS)
                                    for row in result['Derivative Portfolio']]
    return result


def normalize_extracted_funds(funds):
    return {name:normalize_fund_record(data) for name,data in funds.items()}
