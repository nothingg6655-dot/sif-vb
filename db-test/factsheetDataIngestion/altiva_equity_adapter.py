"""Altiva Equity Ex-Top 100's published portfolio and scheme details."""
import re

try:
    from .app import clean, expanded_cell_text, normalized_name
    from .extended_adapters import base_record, find_number, number, validate_extended
except (ImportError, ValueError):
    from app import clean, expanded_cell_text, normalized_name
    from extended_adapters import base_record, find_number, number, validate_extended


def extract_altiva_equity(document):
    name='Altiva Equity Ex-Top 100 Long-Short Fund'
    candidates=[]
    for page in document:
        for table in page.find_tables().tables:
            rows=table.extract()
            if rows and clean(rows[0][0])=='Fund Name' and clean(rows[0][1])==name:
                candidates.append((page,rows))
    if len(candidates)!=1:raise ValueError('No unique Altiva Equity Ex-Top 100 scheme details table.')
    main,rows=candidates[0]
    details={clean(row[0]):clean(row[1]) for row in rows if row[0] and len(row)>1}
    original={clean(row[0]):row[1] or '' for row in rows if row[0] and len(row)>1}
    data=base_record(name,list(document),main)
    data['Investment Objective']=details.get('Investment objective','')
    data['Fund Details']={'Published Scheme Details':details}
    data['Portfolio Scope']='Published equity holdings and TREPS; net current assets included in TREPS'
    for plan in ['Direct','Regular']:
        value=find_number(details.get('NAV (INR)',''),plan+r'\s*[–—-]\s*([\d.]+)')
        if value is not None:data['NAV'][plan+' Plan']=value
        value=find_number(details.get('Expense Ratio*',''),r'BER\s*[–—-]\s*'+plan+r' Plan\s*:\s*([\d.]+)%')
        if value is not None:data['Base Expense Ratio (%)'][plan+' Plan']=value
    aum=details.get('AUM (in INR Cr)','')
    data['AUM (INR Cr)']={'Month End AUM':find_number(aum,r'Month end:\s*Rs\.\s*([\d,.]+)\s*Cr'),
                        'Monthly Average AUM':find_number(aum,r'Monthly Avg:\s*Rs\.\s*([\d,.]+)\s*Cr')}
    scope='Equity'
    for line in original.get('Fund Manager(s)','').splitlines():
        if clean(line) in {'Equity:','Overseas:'}:scope=clean(line).rstrip(':');continue
        match=re.search(r'(?:Mr|Ms)\.\s*([^()|]+)',line)
        if not match:continue
        manager_name=clean(match[1]);marker=re.search(r'\d+$',manager_name)
        manager={'Name':manager_name[:marker.start()].strip() if marker else manager_name,'Role':'Fund Manager - '+scope}
        since=re.search(r'Managing since ([^)]+)',line)
        experience=re.search(r'Experience:\s*(.*)',line)
        if since:manager['Managing Since']=clean(since[1])
        if experience:manager['Experience']=clean(experience[1])
        if marker:
            manager['Published Footnote']=marker[0]
            footnote=re.search(re.escape(manager['Name'])+r' has been appointed as the (.*?) in Altiva Equity Ex-Top 100 Long-Short Fund with effect from ([^.]+)',clean(main.get_text()))
            if footnote:manager.update({'Role':clean(footnote[1])+' - '+scope,'Managing Since':clean(footnote[2])})
        data['Fund Managers'].append(manager)
    holdings=[];raw=[];sector_weights={};dates=set();quants=[]
    for page in document:
        text=page.get_text()
        date=re.search(r'Portfolio holding as on ([^\n]+)',text,re.I)
        if date:dates.add(clean(date[1]))
        for table in page.find_tables().tables:
            cells=table.extract()
            if not cells:continue
            if clean(cells[0][0]).startswith('FY27E'):
                quants.extend([{clean(header):number(value) for header,value in zip(cells[0],row)} for row in cells[1:]])
            if clean(cells[0][0])!='Stock/Sector':continue
            sector=None
            for index,row in enumerate(cells[1:],1):
                if len(row)!=3:raise ValueError('Altiva equity portfolio column count changed.')
                label=expanded_cell_text(page,table.rows[index].cells[0])
                cap=clean(row[1]);value=number(row[2])
                if not label or value is None:raise ValueError('Incomplete Altiva equity portfolio row.')
                raw.append({'Label':label,'Values':[cap,clean(row[2])],'Page':page.number+1})
                if normalized_name(label)=='treps':
                    holdings.append({'Instrument':'TREPS','Category':'Cash & Cash Equivalents','Sector':None,
                                     'Percent to Net Assets':value,'Aggregate':True,'Source Page':page.number+1})
                elif not cap:
                    sector=label;sector_weights[sector]=value
                else:
                    if cap not in {'Large Cap','Mid Cap','Small Cap'} or sector is None:
                        raise ValueError('Unrecognized Altiva equity holding classification: '+label)
                    holdings.append({'Instrument':label,'Category':'Equity & Equity Related','Sector':sector,
                                     'Market Capitalization':cap,'Percent to Net Assets':value,'Source Page':page.number+1})
    if len(dates)!=1:raise ValueError('No unique Altiva equity portfolio reporting date.')
    data['Portfolio Date']=dates.pop()
    data['Portfolio']=holdings;data['Sector Allocation (%)']=sector_weights
    data['Published Portfolio Rows']=raw;data['Published Portfolio Quants']=quants
    data['Published Portfolio Notes']=['Net Current Assets has been adjusted in TREPS.']
    validate_extended(name,data,'edelweiss_altiva_equity_ex_top_100_v1')
    validation=data['Extraction Validation'];checks=validation['Checks'];errors=validation['Errors']
    sector_checks={}
    for sector,published in sector_weights.items():
        values=[h['Percent to Net Assets'] for h in holdings if h['Sector']==sector]
        calculated=round(sum(values),4);tolerance=max(.02,len(values)*.005+.005)
        okay=bool(values) and abs(calculated-published)<=tolerance
        sector_checks[sector]={'Published':published,'Sum of Displayed Holdings':calculated,
                               'Rounding Tolerance':round(tolerance,4),'Within Tolerance':okay}
        if not okay:errors.append(sector+' holdings do not reconcile to the published subtotal.')
    checks['sector_subtotals']=sector_checks
    total=round(sum(h['Percent to Net Assets'] for h in holdings),4)
    tolerance=max(.02,len(holdings)*.005+.005)
    checks['portfolio_grand_total']={'Expected':100,'Sum of Displayed Holdings':total,
                                     'Rounding Tolerance':round(tolerance,4),'Within Tolerance':abs(total-100)<=tolerance}
    if abs(total-100)>tolerance:errors.append('Altiva equity holdings do not reconcile to 100%.')
    if errors:validation['Status']='failed'
    return {name:data}
