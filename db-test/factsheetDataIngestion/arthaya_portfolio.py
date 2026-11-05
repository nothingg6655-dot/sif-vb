"""Arthaya's two-column portfolio: cash, stock futures and net stay separate."""
try:
    from .app import clean, normalized_name
except ImportError:
    from app import clean, normalized_name


def extract_arthaya_portfolio(page, data):
    try:
        from .extended_adapters import SECTORS, published_rows, row_holdings, number
    except ImportError:
        from extended_adapters import SECTORS, published_rows, row_holdings, number

    # The last equity column is net exposure, not the cash security weight.
    equity = published_rows(page, (214, 207, 387, 765), (306, 329, 357))
    holdings, totals, raw = row_holdings(equity, 'multi', sectors=True)
    keys = {normalized_name(label) for label in SECTORS}
    sector_rows = [row for row in equity if normalized_name(row['cells'][0]) in keys]
    data['Sector Allocation (%)'] = {
        row['cells'][0]: number(row['cells'][1]) for row in sector_rows
    }
    continuation = published_rows(page, (400, 207, 573, 375), (483, 514, 541))
    derivatives = []
    category = 'Stock Options'
    option_prefix = clean(page.get_text(clip=(214, 772, 387, 784)))
    cash_key = 'Triparty Repo, Cash, Cash Equivalents & Net Current Assets'
    for row in continuation:
        label = row['cells'][0].lstrip('\uf0fc ')
        value = number(row['cells'][1])
        key = normalized_name(label)
        if key in {'index futures', 'index options', 'treasury bills'}:
            category = {'index futures': 'Index Futures', 'index options': 'Index Options',
                        'treasury bills': 'Treasury Bill'}[key]
            totals[category] = value
        elif key == 'sovereign':
            continue  # This repeats the Treasury Bills subtotal.
        elif key in {normalized_name(cash_key), 'grand total'}:
            totals[cash_key if key != 'grand total' else 'Grand Total'] = value
        elif value is not None:
            holding = {'Instrument': clean(option_prefix + ' ' + label) if category == 'Stock Options' else label,
                       'Category': category, 'Percent to Net Assets': value, 'Sector': None,
                       'Source Page': page.number + 1}
            if category == 'Treasury Bill':
                holdings.append(holding)
            else:
                derivatives.append(holding)
                if category == 'Stock Options':
                    totals['Stock Options'] = value
    data['Derivative Portfolio'] = derivatives
    data['Derivative Portfolio Totals (%)'] = {
        key: totals[key] for key in ['Stock Options', 'Index Futures', 'Index Options'] if key in totals
    }
    data['Portfolio Scope'] = 'Published cash securities; stock futures and net exposure retained in separate columns'

    # Read the named rows in the published exposure table, not pie-slice geometry.
    exposure = published_rows(page, (400, 620, 573, 703), (533,))
    exposure_values = {row['cells'][0]: number(row['cells'][1]) for row in exposure}
    data['Published Portfolio Exposure (%)'] = exposure_values
    asset = {}
    for source, target in [('Unhedged Equity (Non Arbitrage)', 'Unhedged Equity'),
                           ('Hedged Equity (Arbitrage)', 'Hedged Equity')]:
        if source in exposure_values:
            asset[target] = exposure_values[source]
    if 'Treasury Bill' in totals:
        asset['Treasury Bills'] = totals['Treasury Bill']
    if cash_key in totals:
        asset[cash_key] = totals[cash_key]
    data['Asset Allocation (%)'] = asset
    data.setdefault('Supplementary Fields', {})['Arthaya Published Sector Columns'] = {
        'Basis': 'Equity shares, stock futures and net exposure as percentages of net assets',
        'Source Page': page.number + 1,
        'Rows': [{'Label': row['cells'][0], 'Equity Shares': number(row['cells'][1]),
                  'Stock Futures': number(row['cells'][2]), 'Net Exposure': number(row['cells'][3])}
                 for row in sector_rows],
    }
    return holdings, totals, raw + [
        {'Label': row['cells'][0], 'Values': row['cells'][1:], 'Page': row['page']}
        for row in continuation
    ]
