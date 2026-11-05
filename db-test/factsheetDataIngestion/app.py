import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import pymupdf

try:
    from .output_schema import normalize_extracted_funds
    from .fund_registry import FUND_HOUSES
except (ImportError, ValueError):
    from output_schema import normalize_extracted_funds
    from fund_registry import FUND_HOUSES

DEFAULT_PDF = Path(
    r"C:\Users\shris\Aikyam\POC\DynaSIF\Altiva_Hybrid_LongShort_Fund_Portfolio_Update_September_2026_08102026093631.pdf"
)

NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?%?$")
MANAGERS = ("Pranav Mise", "Viral Mehta", "Milan Mody", "Rahul Khetawat", "Mayur Patel")


def clean(value):
    """Collapse PDF line breaks and repeated whitespace without changing content."""
    value = (value or "").replace("�", "-").replace("–", "-").replace("—", "-")
    value = re.sub(r"(\d+(?:st|nd|rd|th))(?=[A-Z])", r"\1 ", value)
    return re.sub(r"\s+", " ", value).strip()


def to_number(value):
    value = clean(value).replace(",", "").removesuffix("%")
    return float(value)


def first_match(pattern, text, default=None, flags=0):
    match = re.search(pattern, text, flags)
    return clean(match.group(1)) if match else default


def normalized_name(value):
    return re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize('NFKC',clean(value)).lower()).strip()


def detect_known_funds(text):
    haystack = normalized_name(text)
    matches = [
        name for name in FUND_HOUSES
        if normalized_name(name) in haystack
        or (name == "Sapphire Equity Long-Short Fund"
            and "sapphire equity long short sif" in haystack)
    ]
    return sorted(matches, key=len, reverse=True)


def page_title(page):
    header = page.get_text(
        "text", clip=pymupdf.Rect(0, 0, page.rect.width, page.rect.height * 0.16), sort=True
    )
    matches = detect_known_funds(header)
    if matches:
        return matches[0]
    match = re.search(r"([A-Za-z0-9]+(?:\s+[A-Za-z0-9&-]+){1,10}\s+Fund)\b", clean(header), re.I)
    return clean(match.group(1)) if match else None


def section_text(text, start, end):
    match = re.search(re.escape(start) + r"(.*?)" + re.escape(end), text, re.S | re.I)
    return clean(match.group(1)) if match else ""


def extract_managers(left_text):
    section = re.search(
        r"Fund Manager Profile(.*?)Fund Details", left_text, re.S | re.I
    )
    if not section:
        return []

    body = section.group(1)
    co_manager_at = body.lower().find("co-fund manager profile")
    managers = []
    for name in MANAGERS:
        match = re.search(re.escape(name) + r"(?:\s*\((for [^)]+)\))?", body, re.I)
        if not match:
            continue
        role = (
            "Co-Fund Manager"
            if co_manager_at >= 0 and match.start() > co_manager_at
            else "Fund Manager"
        )
        if match.group(1):
            role += " - " + clean(match.group(1)).removeprefix("for ")
        managers.append({"Name": name, "Role": role})
    return sorted(managers, key=lambda item: body.find(item["Name"]))


def extract_fund_details(page, left_text):
    details = {}
    for table in page.find_tables().tables:
        rows = table.extract()
        if not rows or clean(rows[0][0]) != "Launch Date":
            continue
        for row in rows:
            if len(row) < 3:
                continue
            key = clean(row[0]).replace("Benchmark Index", "Benchmark")
            value = clean(row[2]).replace("|", "")
            if key and value:
                details[key] = value
        break

    details["Minimum Investment"] = "INR 10,00,000"
    details["Accredited Investor Minimum"] = "INR 1,00,000"
    details["Subscription / Redemption Frequency"] = "Daily (only business days)"
    details["Taxation"] = "Equity Taxation"
    turnover = first_match(r"Portfolio\s*:?[\s\n]+([\d.]+)\s*Turnover Ratio", left_text)
    if turnover:
        details["Portfolio Turnover Ratio"] = float(turnover)
    return details


def words_in_rect(page, rect):
    # Extract whole words first. Passing a tight clip to get_text() can cut off
    # the first digit of a number that touches a detected table border.
    words = page.get_text("words", sort=True)
    return [
        word
        for word in words
        if pymupdf.Rect(rect).contains(
            pymupdf.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2)
        )
    ]


def text_from_words(words):
    return clean(
        " ".join(word[4] for word in sorted(words, key=lambda w: (w[5], w[6], w[7])))
    )


def expand_table_row(page, table, row_index, extracted_row):
    """Split visually separate records that PyMuPDF occasionally merges into one row."""
    cells = table.rows[row_index].cells
    if len(cells) < 3 or not cells[2]:
        return [
            (clean(extracted_row[0]), clean(extracted_row[1]), clean(extracted_row[2]))
        ]

    extracted_values = re.findall(r"-?\d+(?:\.\d+)?%?", extracted_row[2] or "")
    if len(extracted_values) <= 1:
        return [
            (clean(extracted_row[0]), clean(extracted_row[1]), clean(extracted_row[2]))
        ]

    # A merged row's detected cell can cover only its last visual line. Search
    # the complete value column, find the matching sequence, then use adjacent
    # numeric baselines as the true record boundaries.
    column_rect = pymupdf.Rect(cells[2][0], table.bbox[1], cells[2][2], table.bbox[3])
    all_value_words = [
        word
        for word in words_in_rect(page, column_rect)
        if NUMBER_RE.fullmatch(word[4])
    ]
    all_value_words.sort(key=lambda word: (word[1] + word[3]) / 2)
    matches = []
    for start in range(len(all_value_words) - len(extracted_values) + 1):
        candidate = all_value_words[start : start + len(extracted_values)]
        if [word[4] for word in candidate] == extracted_values:
            candidate_y = sum((word[1] + word[3]) / 2 for word in candidate) / len(
                candidate
            )
            cell_y = (cells[2][1] + cells[2][3]) / 2
            matches.append((abs(candidate_y - cell_y), start, candidate))
    if not matches:
        return [
            (clean(extracted_row[0]), clean(extracted_row[1]), clean(extracted_row[2]))
        ]

    _, start, value_words = min(matches)
    all_centers = [(word[1] + word[3]) / 2 for word in all_value_words]
    company_rect = pymupdf.Rect(cells[0][0], table.bbox[1], cells[0][2], table.bbox[3])
    sector_rect = pymupdf.Rect(cells[1][0], table.bbox[1], cells[1][2], table.bbox[3])
    company_words = words_in_rect(page, company_rect)
    sector_words = words_in_rect(page, sector_rect)

    records = []
    for index, value_word in enumerate(value_words):
        global_index = start + index
        center = all_centers[global_index]
        low = (
            (all_centers[global_index - 1] + center) / 2
            if global_index
            else table.bbox[1]
        )
        high = (
            (center + all_centers[global_index + 1]) / 2
            if global_index + 1 < len(all_centers)
            else table.bbox[3]
        )
        in_band = lambda word: low <= (word[1] + word[3]) / 2 < high
        records.append(
            (
                text_from_words([word for word in company_words if in_band(word)]),
                text_from_words([word for word in sector_words if in_band(word)]),
                value_word[4],
            )
        )
    return records


def extract_portfolio(page):
    holdings = []
    totals = {}
    category = None
    grand_total = None
    net_receivables = None

    category_headings = {
        "Equity & Equity Related Total": "Equity & Equity Related",
        "INVIT": "INVIT",
        "Index / Stock Futures": "Index / Stock Futures",
        "Index / Stock Options": "Index / Stock Options",
        "Money Market Instruments": "Money Market Instruments",
        "Treasury Bill": "Treasury Bill",
        "TREPS / Reverse Repo": "TREPS / Reverse Repo",
        "Debt Instruments": "Debt Instruments",
        "Commercial Paper": "Commercial Paper",
        "Commodity Future": "Commodity Future",
        "Silver": "Silver",
    }

    portfolio_tables = []
    for table in page.find_tables().tables:
        rows = table.extract()
        if rows and rows[0] and "Portfolio as on" in clean(rows[0][0]):
            portfolio_tables.append(table)
    portfolio_tables.sort(key=lambda table: table.bbox[0])

    for table in portfolio_tables:
        rows = table.extract()
        for row_index, row in enumerate(rows[2:], start=2):
            for company, sector, value in expand_table_row(page, table, row_index, row):
                if not company:
                    continue

                if company.startswith("Treasury Bill "):
                    category = "Treasury Bill"
                    company = company.removeprefix("Treasury Bill ")

                if company in category_headings and not value:
                    category = category_headings[company]
                    continue
                if company == "Derivatives" or company.startswith("(a) Listed"):
                    continue
                if company == "Sub Total" and value:
                    totals[category or "Unclassified"] = to_number(value)
                    continue
                if company == "Net Receivables / (Payables)" and value:
                    net_receivables = to_number(value)
                    continue
                if company == "GRAND TOTAL" and value:
                    grand_total = to_number(value)
                    continue
                if value and NUMBER_RE.fullmatch(clean(value)):
                    holdings.append(
                        {
                            "Instrument": company,
                            "Sector": sector or None,
                            "Percent to Net Assets": to_number(value),
                            "Category": category,
                        }
                    )

    return holdings, totals, net_receivables, grand_total


def sector_allocation(holdings):
    sectors = defaultdict(float)
    for holding in holdings:
        if holding["Category"] == "Equity & Equity Related" and holding["Sector"]:
            sectors[holding["Sector"]] += holding["Percent to Net Assets"]
    return dict(
        sorted(
            ((key, round(value, 2)) for key, value in sectors.items()),
            key=lambda item: -item[1],
        )
    )


def asset_allocation(totals, net_receivables):
    allocation = {}
    key_map = {
        "Equity & Equity Related": "Equity & Equity Related",
        "INVIT": "INVIT",
        "Treasury Bill": "Treasury Bill",
        "Debt Instruments": "Debt Instruments",
        "Commercial Paper": "Commercial Paper",
        "Commodity Future": "Commodity Future",
        "Silver": "Silver",
    }
    for source, destination in key_map.items():
        if source in totals:
            allocation[destination] = totals[source]

    futures = totals.get("Index / Stock Futures")
    options = totals.get("Index / Stock Options")
    if futures is not None:
        allocation["Derivatives"] = futures
        if options is not None:
            allocation["Index / Stock Options"] = options
    elif options is not None:
        allocation["Derivatives"] = options

    cash = totals.get("TREPS / Reverse Repo", 0) + (net_receivables or 0)
    if cash:
        allocation["Net Receivables / (Payables)"] = round(cash, 2)
    return allocation


def section_bounds(page, title, next_title):
    starts = page.search_for(title)
    ends = page.search_for(next_title)
    if not starts or not ends:
        return None
    return pymupdf.Rect(365, starts[-1].y1, page.rect.width, ends[-1].y0)


def extract_market_capitalization(page):
    region = section_bounds(page, "Market Capitalization", "Asset Allocation")
    if not region:
        return {}
    percent_words = [
        word
        for word in words_in_rect(page, region)
        if re.fullmatch(r"\d+(?:\.\d+)?%", word[4])
    ]
    result = {}
    used = set()
    for label in ("Large Cap", "Mid Cap", "Small Cap"):
        rects = [rect for rect in page.search_for(label) if region.contains(rect.tl)]
        if not rects:
            continue
        label_rect = rects[-1]
        candidates = []
        for index, word in enumerate(percent_words):
            if index in used:
                continue
            x = (word[0] + word[2]) / 2
            y = (word[1] + word[3]) / 2
            dx = x - (label_rect.x0 + label_rect.x1) / 2
            dy = y - (label_rect.y0 + label_rect.y1) / 2
            candidates.append((abs(dy) * 3 + abs(dx), index, word))
        if candidates:
            _, index, word = min(candidates)
            used.add(index)
            result[label] = to_number(word[4])
    return result


def extract_sector_allocation(page):
    region = section_bounds(page, "Sector Allocation", "Market Capitalization")
    if not region:
        return {}
    words = words_in_rect(page, region)
    result = {}
    for value_word in words:
        if not re.fullmatch(r"\d+\.\d+%", value_word[4]):
            continue
        label_words = [
            word
            for word in words
            if word[2] < value_word[0]
            and abs(word[1] - value_word[1]) < 1.2
            and not word[4].endswith("%")
        ]
        label = clean(
            " ".join(word[4] for word in sorted(label_words, key=lambda word: word[0]))
        )
        if label:
            result[label] = to_number(value_word[4])
    return dict(sorted(result.items(), key=lambda item: -item[1]))


def extract_rating_profile(page):
    titles = page.search_for("Rating Profile")
    if not titles:
        return {}
    try:
        from .extended_adapters import line_words, number
    except (ImportError, ValueError):
        from extended_adapters import line_words, number
    bounds=(365,titles[-1].y1,page.rect.width,page.rect.height)
    words=[w for _,ws in line_words(page,bounds) for w in ws]
    labels=[w for w in words if re.fullmatch(r'AAA?|AA[+\-]|A[12][+\-]?|SOVEREIGN',w[4],re.I)]
    values=[w for w in words if re.fullmatch(r'-?\d+(?:\.\d+)?%',w[4])]
    result={};used=set()
    for label in labels:
        choices=[(abs((v[1]+v[3]-label[1]-label[3])/2)*3+abs((v[0]+v[2]-label[0]-label[2])/2),i,v)
                 for i,v in enumerate(values) if i not in used and abs(v[1]-label[1])<22]
        if choices:
            _,i,v=min(choices);used.add(i);result[label[4]]=number(v[4])
    if result:return result
    region = pymupdf.Rect(365, titles[-1].y1, page.rect.width, page.rect.height)
    label_blocks = []
    value_blocks = []
    for block in page.get_text("blocks", sort=True):
        block_rect = pymupdf.Rect(block[:4])
        if not block_rect.intersects(region):
            continue
        text = clean(block[4])
        if re.fullmatch(r"-?\d+\.\d+%", text):
            value_blocks.append((block_rect, text))
        elif text:
            label_blocks.append((block_rect, text))

    ratings = {}
    used = set()
    for label_rect, label in label_blocks:
        candidates = []
        for index, (value_rect, value) in enumerate(value_blocks):
            if index in used:
                continue
            dx = value_rect.x0 - label_rect.x0
            dy = value_rect.y0 - label_rect.y0
            candidates.append((abs(dy) * 3 + abs(dx), index, value))
        if candidates:
            _, index, value = min(candidates)
            used.add(index)
            ratings[label] = to_number(value)
    return ratings


def extract_core_data(page):
    left_text = page.get_text(
        "text", clip=pymupdf.Rect(0, 70, 155, page.rect.height), sort=True
    )
    holdings, totals, net_receivables, grand_total = extract_portfolio(page)
    details = extract_fund_details(page, left_text)

    nav = {}
    for label in (
        "Regular Plan IDCW",
        "Direct Plan IDCW",
        "Regular Plan",
        "Direct Plan",
    ):
        value = first_match(
            rf"^\s*{re.escape(label)}\s+([\d.]+)\s*$", left_text, flags=re.M
        )
        if value:
            nav[label] = float(value)

    ter = {}
    ter_text = left_text[left_text.find("Base Expense Ratio") :]
    for label in ("Regular Plan", "Direct Plan"):
        value = first_match(
            rf"^\s*{re.escape(label)}\s+([\d.]+)%\s*$", ter_text, flags=re.M
        )
        if value:
            ter[label] = float(value)

    result = {
        "Investment Objective": section_text(
            left_text, "Investment Objective", "Fund Manager Profile"
        ),
        "Fund Managers": extract_managers(left_text),
        "Fund Details": details,
        "AUM (INR Cr)": {
            "Net AUM": float(first_match(r"Net AUM\s+([\d,.]+)", left_text)),
            "Monthly Average AUM": float(
                first_match(r"Monthly Average AUM\s+([\d,.]+)", left_text)
            ),
        },
        "NAV": nav,
        "Base Expense Ratio (%)": ter,
        "Portfolio": holdings,
        "Portfolio Totals (%)": {
            **totals,
            "Net Receivables / (Payables)": net_receivables,
            "Grand Total": grand_total,
        },
        "Sector Allocation (%)": extract_sector_allocation(page),
        "Market Capitalization (%)": extract_market_capitalization(page),
        "Asset Allocation (%)": asset_allocation(totals, net_receivables),
    }
    ratings = extract_rating_profile(page)
    if ratings:
        result["Rating Profile (%)"] = ratings

    if "Statistical Debt Indicators" in left_text:
        result["Statistical Debt Indicators"] = {
            "Annualised Portfolio YTM": first_match(
                r"Annualised Portfolio YTM\s*:?\s*([\d.]+%)", left_text
            ),
            "Macaulay Duration": first_match(
                r"Macaulay Duration\s*:?\s*([\d.]+\s*years)", left_text
            ),
            "Residual Maturity": first_match(
                r"Residual Maturity\s*:?\s*([\d.]+\s*years)", left_text
            ),
        }
    return result


def extract_risk_and_performance(page, fund_data):
    text = clean(page.get_text("text", sort=True))
    levels = re.findall(r"Risk band level\s+(\d+)", text, re.I)
    benchmark = fund_data["Fund Details"].get("Benchmark")
    fund_data["Risk Band & Strategy"] = {
        "Investment Strategy Risk Band": f"Level {levels[0]}" if levels else None,
        "Benchmark Risk Band": f"Level {levels[1]}" if len(levels) > 1 else None,
        "Benchmark": benchmark,
    }

    for table in page.find_tables().tables:
        rows = table.extract()
        if not rows or clean(rows[0][0]) != "Scheme Performance":
            continue
        headers = [
            "Scheme",
            "Last 6 Months (%)",
            "PTP - Last 6 Months (INR)",
            "Since Inception (%)",
            "PTP - Since Inception (INR)",
        ]
        performance = []
        for row in rows[2:]:
            if not row[0]:
                continue
            performance.append(
                {headers[i]: clean(row[i]) for i in range(min(len(headers), len(row)))}
            )
        if performance:
            fund_data["Scheme Performance"] = performance


def extract_dynasif(document):
    funds = {}
    for page in document:
        title = page_title(page)
        if not title:
            continue
        if "Investment Objective" in page.get_text("text"):
            funds[title] = extract_core_data(page)
        elif title in funds:
            extract_risk_and_performance(page, funds[title])
    return funds


def expanded_cell_text(page, cell, left_padding=7):
    if not cell:
        return ""
    rect = pymupdf.Rect(cell)
    rect.x0 = max(0, rect.x0 - left_padding)
    return text_from_words(words_in_rect(page, rect))


def weight_table_rows(page, table, category):
    """Read one or two Name/Weight column pairs without clipping border glyphs."""
    extracted = table.extract()
    if len(extracted) < 2:
        return []
    column_pairs = [(0, 1), (3, 4)] if len(extracted[0]) >= 5 else [(0, 1)]
    holdings = []
    for row_index, row in enumerate(extracted[1:], start=1):
        cells = table.rows[row_index].cells
        for name_index, value_index in column_pairs:
            if name_index >= len(cells) or value_index >= len(row):
                continue
            name = expanded_cell_text(page, cells[name_index])
            value = clean(row[value_index])
            if name and value and NUMBER_RE.fullmatch(value):
                holdings.append(
                    {
                        "Instrument": name,
                        "Sector": None,
                        "Percent to Net Assets": to_number(value),
                        "Category": category,
                    }
                )
    return holdings


def split_parallel_values(value):
    return [clean(part) for part in (value or "").splitlines() if clean(part)]


def altiva_managers(rows):
    managers = []
    for row in rows:
        if len(row) < 5 or clean(row[1]) not in {"Equity", "Debt", "Overseas"}:
            continue
        role = clean(row[1])
        names = split_parallel_values(row[2])
        experience = split_parallel_values(row[3])
        since = split_parallel_values(row[4])
        for index, name in enumerate(names):
            managers.append(
                {
                    "Name": name.removeprefix("Mr. "),
                    "Role": f"Fund Manager - {role}",
                    "Experience": experience[index] if index < len(experience) else None,
                    "Managing Since": since[index] if index < len(since) else None,
                }
            )
    return managers


def altiva_details(page):
    details_table = None
    for table in page.find_tables().tables:
        rows = table.extract()
        if rows and clean(rows[0][0]) == "Investment objective":
            details_table = rows
            break
    if not details_table:
        return {}, {}, {}, [], None

    row_map = {clean(row[0]): clean(row[1]) for row in details_table if row and row[0] and len(row) > 1}
    details = {
        "Benchmark": row_map.get("Benchmark"),
        "Type of Investment Strategy": row_map.get("Type of investment strategy"),
        "Subscription / Redemption Frequency": row_map.get("Subscription/Redemption frequency"),
        "Plan & Options": row_map.get("Plan & Options"),
        "Load Structure": row_map.get("Exit load"),
        "Minimum Investment": row_map.get("Min application amount"),
        "Features": row_map.get("Features"),
        "Minimum SIP / STP / SWP": next(
            (value for key, value in row_map.items() if key.startswith("Min investment in SIP")), None
        ),
        "Inception Date": row_map.get("Inception Date"),
    }
    details = {key: value for key, value in details.items() if value}

    nav_text = row_map.get("NAV (INR)", "")
    nav = {}
    for plan in ("Direct", "Regular"):
        value = first_match(rf"{plan}\s*[–-]\s*([\d.]+)", nav_text, flags=re.I)
        if value:
            nav[f"{plan} Plan"] = float(value)

    expense_text = row_map.get("Expense Ratio*", "")
    expense = {}
    for plan in ("Direct", "Regular"):
        value = first_match(rf"BER\s*\({plan} Plan\)\s*[–-]\s*([\d.]+)%", expense_text, flags=re.I)
        if value:
            expense[f"{plan} Plan"] = float(value)

    aum = first_match(r"Month End:\s*Rs\.\s*([\d,]+)\s*Cr", row_map.get("AUM", ""), flags=re.I)
    return details, nav, expense, altiva_managers(details_table), float(aum.replace(",", "")) if aum else None


def tabular_records(table):
    rows = table.extract()
    if len(rows) < 2:
        return []
    headers = [clean(value) or f"Column {index + 1}" for index, value in enumerate(rows[0])]
    return [
        {headers[index]: clean(value) for index, value in enumerate(row) if index < len(headers)}
        for row in rows[1:]
        if any(clean(value) for value in row)
    ]


def labeled_block_values(page, labels):
    blocks = [
        (pymupdf.Rect(block[:4]), clean(block[4]))
        for block in page.get_text("blocks", sort=True)
        if clean(block[4])
    ]
    result = {}
    used = set()
    for label in labels:
        label_blocks = [(rect, text) for rect, text in blocks if normalized_name(text) == normalized_name(label)]
        if not label_blocks:
            continue
        label_rect = label_blocks[0][0]
        candidates = []
        for index, (rect, text) in enumerate(blocks):
            if index in used or not re.fullmatch(r"-?\d+(?:\.\d+)?(?:%|\s*yr(?:s|ears)?)", text, re.I):
                continue
            dx = (rect.x0 + rect.x1) / 2 - (label_rect.x0 + label_rect.x1) / 2
            dy = rect.y0 - label_rect.y1
            if -2 <= dy <= 35:
                candidates.append((abs(dx) + abs(dy) * 3, index, text))
        if candidates:
            _, index, value = min(candidates)
            used.add(index)
            result[label] = value
    return result


def extract_altiva(document):
    fund_name = "Altiva Hybrid Long-Short Fund"
    holdings = []
    totals = {}
    summary = {}
    details = {}
    nav = {}
    expense = {}
    managers = []
    aum = None
    objective = ""
    portfolio_date = None
    monthly_performance = []
    scheme_performance = []
    idcw_history = {"Regular Plan": [], "Direct Plan": []}
    debt_quants = {}

    for page in document:
        text = page.get_text("text", sort=True)
        compact = clean(text)

        if "Portfolio summary as on" in text:
            portfolio_date = first_match(r"Portfolio summary as on\s+([^\n]+)", text, flags=re.I)
            labels = {
                "Cash Future Arbitrage & Covered Call": "Cash Future Arbitrage & Covered Call",
                "Fixed Income": "Fixed Income",
                "Special Situation": "Special Situation",
                "Other Derivative Strategies": "Other Derivative Strategies",
                "REITs/InvITs": "REITs / InvITs",
                "Others": "Others",
                "TOTAL": "Total",
            }
            for source, destination in labels.items():
                value = first_match(rf"{re.escape(source)}[*^]?\s+(\d+(?:\.\d+)?)%", compact, flags=re.I)
                if value:
                    summary[destination] = float(value)

        if "EQUITY & EQUITY RELATED:" in text and "Portfolio Commentary" in text:
            value = first_match(r"EQUITY\s*&\s*EQUITY RELATED:\s*(-?[\d.]+)%", text, flags=re.I)
            if value:
                totals["Equity & Equity Related"] = float(value)
            for table in page.find_tables().tables:
                holdings.extend(weight_table_rows(page, table, "Equity & Equity Related"))

        if "INDEX/STOCK OPTIONS:" in text:
            category_ranges = (
                ("Equity & Equity Related", 0, 160),
                ("Index / Stock Options", 160, 525),
                ("Index / Stock Futures", 525, 720),
                ("REITs", 720, page.rect.height),
            )
            for label, pattern in (
                ("Index / Stock Options", r"INDEX/STOCK OPTIONS:\s*(-?[\d.]+)%"),
                ("Index / Stock Futures", r"INDEX/STOCK FUTURES:\s*(-?[\d.]+)%"),
                ("REITs", r"REITs:\s*(-?[\d.]+)%"),
                ("InvITs", r"InvITs:\s*(-?[\d.]+)%"),
            ):
                value = first_match(pattern, text, flags=re.I)
                if value:
                    totals[label] = float(value)
            for table in page.find_tables().tables:
                y = table.bbox[1]
                if y >= 720:
                    category = "REITs" if table.bbox[0] < page.rect.width / 2 else "InvITs"
                else:
                    category = next(label for label, low, high in category_ranges if low <= y < high)
                holdings.extend(weight_table_rows(page, table, category))

        if "FIXED INCOME:" in text:
            value = first_match(r"FIXED INCOME:\s*(-?[\d.]+)%", text, flags=re.I)
            if value:
                totals["Fixed Income"] = float(value)
            tables = page.find_tables().tables
            if tables:
                holdings.extend(weight_table_rows(page, tables[0], "Fixed Income"))
            debt_quants = labeled_block_values(
                page,
                ("Yield to maturity (YTM)", "Modified Duration", "Average Maturity", "Macaulay Duration"),
            )
            if len(tables) > 1:
                monthly_performance = tabular_records(tables[1])

        if "Scheme details" in text:
            details, nav, expense, managers, aum = altiva_details(page)
            objective = next(
                (
                    clean(row[1])
                    for table in page.find_tables().tables
                    for row in table.extract()
                    if row and clean(row[0]) == "Investment objective"
                ),
                "",
            )
            tables = page.find_tables().tables
            if len(tables) >= 3:
                idcw_history["Regular Plan"] = tabular_records(tables[0])
                idcw_history["Direct Plan"] = tabular_records(tables[1])
                performance_rows = tables[2].extract()
                if len(performance_rows) >= 3:
                    for row in performance_rows[2:]:
                        scheme_performance.append(
                            {
                                "Period": clean(row[0]),
                                "Direct Annualized Returns": clean(row[1]),
                                "Direct Value of INR 10,000": clean(row[2]),
                                "Regular Annualized Returns": clean(row[3]),
                                "Regular Value of INR 10,000": clean(row[4]),
                                "Benchmark Annualized Returns": clean(row[5]),
                                "Benchmark Value of INR 10,000": clean(row[6]),
                            }
                        )

    result = {
        fund_name: {
            "AMC / Fund House": FUND_HOUSES[fund_name],
            "Portfolio Date": portfolio_date,
            "Investment Objective": objective,
            "Fund Managers": managers,
            "Fund Details": details,
            "AUM (INR Cr)": {"Month End AUM": aum},
            "NAV": nav,
            "Base Expense Ratio (%)": expense,
            "Portfolio": holdings,
            "Portfolio Totals (%)": totals,
            "Portfolio Summary (%)": summary,
            "Debt Portfolio Quants": {key: value for key, value in debt_quants.items() if value},
            "Monthly Rolling Performance": monthly_performance,
            "Scheme Performance": scheme_performance,
            "IDCW History": idcw_history,
        }
    }
    return result


def validate_fund(fund_name, data, adapter):
    errors = []
    warnings = []
    checks = {}

    checks["recognized_fund"] = fund_name in FUND_HOUSES
    checks["portfolio_found"] = bool(data.get("Portfolio"))
    checks["nav_found"] = bool(data.get("NAV"))
    checks["aum_found"] = any(value is not None for value in data.get("AUM (INR Cr)", {}).values())
    if not all((checks["recognized_fund"], checks["portfolio_found"], checks["nav_found"], checks["aum_found"])):
        errors.append("One or more required sections (fund identity, portfolio, NAV, AUM) were not found.")

    totals = data.get("Portfolio Totals (%)", {})
    grouped = defaultdict(list)
    for holding in data.get("Portfolio", []):
        grouped[holding.get("Category")].append(holding.get("Percent to Net Assets", 0))
    subtotal_checks = {}
    for category, published in totals.items():
        if category not in grouped:
            continue
        calculated = round(sum(grouped[category]), 3)
        tolerance = max(0.02, len(grouped[category]) * 0.005 + 0.005)
        subtotal_checks[category] = {
            "calculated_from_displayed_rows": calculated,
            "published": published,
            "within_rounding_tolerance": abs(calculated - published) <= tolerance,
        }
        if abs(calculated - published) > tolerance:
            errors.append(f"{category} holdings do not reconcile to the published subtotal.")
    checks["portfolio_subtotals"] = subtotal_checks

    for label in ("Market Capitalization (%)", "Asset Allocation (%)", "Rating Profile (%)"):
        values = data.get(label)
        if values:
            displayed_sum = round(sum(values.values()), 3)
            if not 99.95 <= displayed_sum <= 100.05:
                warnings.append(f"{label} extracted values sum to {displayed_sum}%; this allocation may be incomplete. Consult Published Pages.")

    status = "failed" if errors else ("passed_with_warnings" if warnings else "passed")
    return {
        "Status": status,
        "Adapter": adapter,
        "Checks": checks,
        "Errors": errors,
        "Warnings": warnings,
    }


def extract_factsheet(pdf_path):
    try:
        from .risk_bands import enrich_risk_bands
    except ImportError:
        from risk_bands import enrich_risk_bands
    try:
        from .market_cap import enrich_market_cap
    except ImportError:
        from market_cap import enrich_market_cap
    try:
        from .allocations import enrich_allocations
    except ImportError:
        from allocations import enrich_allocations
    document = pymupdf.open(pdf_path)
    if not any(page.get_text("text").strip() for page in document):
        document.close()
        raise ValueError("The PDF has no usable text layer. OCR is required before exact extraction.")

    document_text = "\n".join(page.get_text("text") for page in document)
    detected = detect_known_funds(document_text)
    if "DynaSIF" in document_text:
        funds = extract_dynasif(document)
        adapter = "360_one_dynasif_v1"
    elif "Altiva Hybrid Long-Short Fund" in document_text:
        funds = extract_altiva(document)
        adapter = "edelweiss_altiva_hybrid_v1"
    else:
        try:
            from .extended_adapters import extract_extended
        except (ImportError, ValueError):
            from extended_adapters import extract_extended
        houses={FUND_HOUSES[name] for name in detected}
        if len(houses)!=1:
            document.close()
            raise ValueError('Cannot identify one registered AMC in this PDF.')
        funds=extract_extended(document,houses.pop())
        enrich_market_cap(document, funds)
        enrich_allocations(document, funds)
        enrich_risk_bands(document, funds)
        document.close()
        if not funds:raise ValueError('No verified layout matched the detected registered schemes.')
        return normalize_extracted_funds(funds)
    try:
        from .extended_adapters import evidence
    except (ImportError, ValueError):
        from extended_adapters import evidence
    for fund_name,data in funds.items():
        fund_pages=(list(document) if adapter=='edelweiss_altiva_hybrid_v1' else
                    [p for p in document if fund_name in detect_known_funds(p.get_text())])
        data['Published Pages']=evidence(fund_pages)
        data['Source Pages']=[p.number+1 for p in fund_pages]
    for fund_name, data in funds.items():
        data.setdefault("AMC / Fund House", FUND_HOUSES.get(fund_name))
        data["Extraction Validation"] = validate_fund(fund_name, data, adapter)
    enrich_market_cap(document, funds)
    enrich_allocations(document, funds)
    enrich_risk_bands(document, funds)
    document.close()
    return normalize_extracted_funds(funds)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract validated structured data from supported SIF factsheets."
    )
    parser.add_argument(
        "pdf",
        nargs="?",
        type=Path,
        default=DEFAULT_PDF,
        help="Path to the factsheet PDF",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("extracted_factsheet.json"),
        help="JSON output path (default: extracted_factsheet.json)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.pdf.is_file():
        raise FileNotFoundError(f"PDF not found: {args.pdf}")
    extracted = extract_factsheet(args.pdf)
    output = json.dumps(extracted, indent=2, ensure_ascii=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output + "\n", encoding="utf-8")
    print(f"Extracted {len(extracted)} funds to {args.output.resolve()}")


if __name__ == "__main__":
    main()
