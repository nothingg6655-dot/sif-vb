"""Date parsing and normalization utilities for factsheet reporting dates."""
import calendar
from datetime import date, datetime
import re

MONTH_NAMES = {name.lower(): idx for idx, name in enumerate(calendar.month_name) if name}
MONTH_ABBRS = {name.lower(): idx for idx, name in enumerate(calendar.month_abbr) if name}


def last_day_of_month(year: int, month: int) -> date:
    days = calendar.monthrange(year, month)[1]
    return date(year, month, days)


def parse_reporting_month(value: str | date | datetime | None) -> date | None:
    """Normalize any factsheet date or period into the first day of the month (YYYY-MM-01)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return date(value.year, value.month, 1)
    if isinstance(value, date):
        return date(value.year, value.month, 1)

    text = str(value).strip().lower()
    # Match YYYY-MM or YYYY-MM-DD
    iso_match = re.match(r'^(\d{4})-(\d{1,2})(?:-\d{1,2})?$', text)
    if iso_match:
        year = int(iso_match.group(1))
        month = int(iso_match.group(2))
        return date(year, month, 1)

    # Match month name and 4-digit year (e.g. 'september 2026', '30th september 2026', 'september 30, 2026')
    year_match = re.search(r'\b(20\d\d)\b', text)
    if not year_match:
        return None
    year = int(year_match.group(1))

    for name, month_num in {**MONTH_NAMES, **MONTH_ABBRS}.items():
        if re.search(r'\b' + re.escape(name) + r'\b', text):
            return date(year, month_num, 1)

    return None


def parse_portfolio_as_of(value: str | date | datetime | None) -> date | None:
    """Extract the specific portfolio as-of date (e.g., 2026-09-30)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip().lower()
    # Check for YYYY-MM-DD
    iso_match = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', text)
    if iso_match:
        return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))

    # If YYYY-MM, return end of month
    iso_month_match = re.match(r'^(\d{4})-(\d{1,2})$', text)
    if iso_month_match:
        return last_day_of_month(int(iso_month_match.group(1)), int(iso_month_match.group(2)))

    # Look for '30th September 2026', 'September 30, 2026', '30 September 2026'
    day_month_year = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\s+(\d{4})', text)
    if day_month_year:
        day = int(day_month_year.group(1))
        month_name = day_month_year.group(2)
        year = int(day_month_year.group(3))
        month = MONTH_NAMES.get(month_name) or MONTH_ABBRS.get(month_name)
        if month and 1 <= day <= 31:
            return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    month_day_year = re.search(r'([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})', text)
    if month_day_year:
        month_name = month_day_year.group(1)
        day = int(month_day_year.group(2))
        year = int(month_day_year.group(3))
        month = MONTH_NAMES.get(month_name) or MONTH_ABBRS.get(month_name)
        if month and 1 <= day <= 31:
            return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    # Fallback to reporting month end
    reporting_month = parse_reporting_month(value)
    if reporting_month:
        return last_day_of_month(reporting_month.year, reporting_month.month)

    return None


def parse_flexible_date(value: str | date | datetime | None) -> date | None:
    """Parse dates such as launch dates, as_of dates, managing_since dates."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value).strip().lower()
    # Try ISO
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass

    # Try common formats
    for fmt in ('%B %d, %Y', '%d %B %Y', '%d %B, %Y', '%B %Y', '%b %d, %Y',
                '%d-%b-%Y', '%b-%y', '%b-%Y', '%d/%m/%Y', '%Y/%m/%d'):
        try:
            cleaned = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', text)
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass

    return parse_portfolio_as_of(text)
