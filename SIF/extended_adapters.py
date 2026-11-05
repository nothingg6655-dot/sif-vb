"""Verified SIF page layouts, with source evidence and explicit missing fields.

Keep cash holdings and derivatives in separate columns. A factsheet can publish
only top positions, or refer NAV/TER to another page; these are not invented.
"""
import re
from collections import defaultdict

import pymupdf

from app import clean, detect_known_funds, normalized_name
from fund_registry import FUND_HOUSES

NUMBER = re.compile(r'^[+\-–−]?\d[\d,]*(?:\.\d+)?%?$')
SECTORS = {
    'Automobile and Auto Components','Automobile And Auto Components','Capital Goods','Construction',
    'Consumer Durables','Consumer Services','Financial Services','Healthcare','Information Technology',
    'Metals & Mining','Oil Gas & Consumable Fuels','Others','Realty','Services','Telecommunication',
    'Utilities','Fast Moving Consumer Goods','Power','BANKS','AUTOMOBILES','IT - SOFTWARE',
    'RETAILING','PHARMACEUTICALS & BIOTECHNOLOGY','HEALTHCARE SERVICES','FINANCE',
    'TELECOM - SERVICES','PETROLEUM PRODUCTS','CAPITAL MARKETS','NON - FERROUS METALS',
    'CHEMICALS & PETROCHEMICALS','CONSUMER DURABLES','ELECTRICAL EQUIPMENT','POWER',
    'INDUSTRIAL PRODUCTS','AUTO COMPONENTS','TRANSPORT INFRASTRUCTURE',
    'AGRICULTURAL, COMMERCIAL & CONSTRUCTION VEHICLES','FINANCIAL TECHNOLOGY (FINTECH)',
}
SECTORS.update({'Aerospace & Defense','Agricultural Food & Other Products','Agricultural, Commercial & Construction Vehicles',
 'Auto Components','Automobiles','Banks','Beverages','Capital Markets','Cement & Cement Products','Chemicals & Petrochemicals',
 'Commercial Services & Supplies','Consumable Fuels','Diversified FMCG','Diversiﬁed Fmcg','Electrical Equipment','Ferrous Metals',
 'Fertilizers & Agrochemicals','Finance','Financial Technology (Fintech)','Food Products','Gas','Healthcare Services',
 'Industrial Products','Insurance','IT - Software','Leisure Services','Minerals & Mining','Non - Ferrous Metals',
 'Oil','Personal Products','Petroleum Products','Pharmaceuticals & Biotechnology','Retailing','Telecom - Services',
 'Transport Services','Transport Infrastructure','Textiles & Apparels','Paper, Forest & Jute Products',
 'Industrial Manufacturing','Agricultural Food & other Products','Media, Entertainment & Publication'})
SECTORS.update({'Healthcare Equipment & Supplies','IT - Services','Telecommunications Equipment','Other Consumer Services',
 'Agricultural, Commercial Construction Vehicles','Agricultural, Commercial & Construction Vehicles'})


def number(value):
    text=clean(value).replace('–','-').replace('−','-').replace(',','').removesuffix('%')
    if text.startswith('(') and text.endswith(')'): text='-'+text[1:-1]
    return float(text) if NUMBER.fullmatch(text) else None


def clip_text(page, rect):
    return page.get_text('text',clip=pymupdf.Rect(rect),sort=True)


def line_words(page, rect=None):
    words=page.get_text('words')
    if rect:
        region=pymupdf.Rect(rect)
        words=[w for w in words if region.contains(pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2))]
    lines=[]
    for word in sorted(words,key=lambda w:((w[1]+w[3])/2,w[0])):
        center=(word[1]+word[3])/2
        if not lines or abs(center-lines[-1][0])>2:
            lines.append([center,[word]])
        else:
            lines[-1][1].append(word)
    return [(y,sorted(ws,key=lambda w:w[0])) for y,ws in lines]


def find_number(text, pattern):
    match=re.search(pattern,text,re.I|re.S)
    return number(match[1]) if match else None


def field_on_line(page,label,rect):
    wanted=normalized_name(label)
    for _,words in line_words(page,rect):
        label_words=[w[4] for w in words if number(w[4]) is None]
        if normalized_name(' '.join(label_words)).startswith(wanted):
            values=[number(w[4]) for w in words if NUMBER.fullmatch(w[4])]
            if values: return values[-1]
    return None


def published_rows(page,rect,cuts):
    """Anchor each table row on its numeric cells, then assign wrapped label lines.

    Numeric values are often vertically centred beside a two-line name. Grouping
    solely by text baseline shifts those names onto the preceding security.
    """
    lines=[]
    headers=['security name','name of the instrument','company issuer','company instrument','issuer name',
             'company name','instrument name','outstanding derivative','no of shares','market value','long short']
    category_keys={normalized_name(x) for x in SECTORS|{'Equity Shares','Equity','Equity Futures','Corporate Bond',
        'Real Estate Investment Trust','Infrastructure Investment Trust','Non Convertible Debentures',
        'Non-Convertible Debentures','Non-Convertible Preference Share','Government Securities','Treasury Bills',
        'Certificate of Deposit','Securitised Debt Amort','Mutual Fund Units','REITs','InvITs','Cash Equivalent'}}
    for y,words in line_words(page,rect):
        cells=['']*(len(cuts)+1)
        for w in words:
            col=sum((w[0]+w[2])/2>=cut for cut in cuts)
            cells[col]+=' '+w[4]
        cells=[clean(c) for c in cells]
        key=normalized_name(' '.join(cells))
        if any(h in key for h in headers) or re.search(r'Top Ten Holdings|Value Less than|Derivatives are considered', ' '.join(cells),re.I):continue
        numeric=any(number(c) is not None or c in {'-','^'} for c in cells[1:])
        lines.append({'cells':cells,'y':y,'numeric':numeric})
    rows=[]
    for line in lines:
        if not line['numeric']:continue
        if rows and abs(line['y']-rows[-1]['y'])<3:
            for i,c in enumerate(line['cells']):
                if c:rows[-1]['cells'][i]=clean(rows[-1]['cells'][i]+' '+c)
        else:rows.append({'cells':line['cells'][:],'y':line['y'],'page':page.number+1,'parts':[(line['y'],line['cells'][0])]})
    for line in lines:
        if line['numeric']:continue
        label=line['cells'][0]
        if not label:
            # Wrapped ratings and industries have no issuer text on that line.
            numeric_rows=[r for r in rows if any(number(c) is not None or c in {'-','^'} for c in r['cells'][1:])]
            if numeric_rows and any(line['cells'][1:]):
                target=min(numeric_rows,key=lambda r:abs(r['y']-line['y']))
                if abs(target['y']-line['y'])<=12:
                    for i,c in enumerate(line['cells'][1:],1):
                        if c:target['cells'][i]=clean(target['cells'][i]+' '+c)
            continue
        if normalized_name(label) in category_keys or re.match(r'^Non.?Convertible|^Preference Share|^Certificate of Dep|^Treasury Bill',label,re.I):
            rows.append({'cells':line['cells'],'y':line['y'],'page':page.number+1,'parts':[(line['y'],label)]})
            continue
        numeric_rows=[r for r in rows if any(number(c) is not None or c in {'-','^'} for c in r['cells'][1:])]
        if not numeric_rows:continue
        target=min(numeric_rows,key=lambda r:abs(r['y']-line['y']))
        if abs(target['y']-line['y'])<=12:
            target['parts'].append((line['y'],label))
            for i,c in enumerate(line['cells'][1:],1):
                if c:target['cells'][i]=clean(target['cells'][i]+' '+c)
    for row in rows:
        row['cells'][0]=clean(' '.join(label for _,label in sorted(row.pop('parts',[]))))
        row['y']=round(row['y'],2)
        for i,c in enumerate(row['cells'][1:],1):
            mixed=re.fullmatch(r'(.*?)\s+([+\-−]?\d[\d,.]*%?)',c)
            if mixed and number(mixed[2]) is not None:
                row['cells'][0]=clean(row['cells'][0]+' '+mixed[1])
                row['cells'][i]=mixed[2]
    rows=sorted(rows,key=lambda r:r['y'])
    for i in range(len(rows)-1,0,-1):
        if normalized_name(rows[i-1]['cells'][0])=='non convertible' and normalized_name(rows[i]['cells'][0])=='preference share':
            rows[i-1]['cells'][0]+=' '+rows[i]['cells'][0];rows.pop(i)
    return rows


def row_holdings(rows, style='single', initial='Equity & Equity Related', sectors=False):
    holdings=[]
    totals={}
    sector=None
    category=initial
    subtotal_labels={normalized_name(s) for s in SECTORS}
    categories={
        'equity futures':'Equity Futures','corporate bond':'Corporate Bond',
        'certificate of dep osite':'Certificate of Deposit','certificate of deposite':'Certificate of Deposit',
        'treasury bill':'Treasury Bill','treasury bills':'Treasury Bill',
        'equity shares':'Equity & Equity Related','equity':'Equity & Equity Related',
        'real estate investment trust':'REITs','infrastructure investment trust':'InvITs',
        'non convertible debentures':'Non Convertible Debentures',
        'non convertible preference share':'Preference Shares','government securities':'Government Securities',
        'equity equity related':'Equity & Equity Related',
        'equity equity related instruments':'Equity & Equity Related',
        'sovereign':'Treasury Bill',
        'certificate of deposit cds':'Certificate of Deposit',
        'certi cate of deposit cds':'Certificate of Deposit',
        'debt holdings':'Debt Holdings','corporate securities':'Corporate Securities',
        'government securities long term':'Government Securities',
        'short term':'Government Securities','long term':'Government Securities',
        'units of infrastructure investment trusts invits':'InvITs',
        'index futures options':'Index Futures / Options','options':'Options',
    }
    published=[]
    for item in rows:
        cells=item['cells']
        label=clean(cells[0]).lstrip('•\uf03d\uf03b\uf03c\uf03e\uf03f\uf03a\uf03d\uf03b\uf03d\uf03d ')
        key=normalized_name(label)
        key=key.removesuffix(' long term') if key.startswith('government securities ') else key
        values=[number(c) if c!='-' else 0.0 for c in cells[1:]]
        published.append({'Label':label,'Values':cells[1:],'Page':item['page']})
        if key in categories:
            category=categories[key]
            sector=None
            usable=[v for v in values if v is not None]
            if usable: totals[label if key in {'short term','long term'} else category]=usable[0]
            continue
        if sectors and key in subtotal_labels:
            sector=label
            if category in {'Index Futures / Options','Options'}:category='Equity & Equity Related'
            continue
        if re.search(r'\btotal\b',label,re.I) or key in {'net cash and cash equivalent','cash cash equivalents derivative margin and others',
             'cash cash equivalent','short term debt net current assets','short term debt and net current assets',
             'net current assets','cash call treps term deposits','triparty repo cash cash equivalents net current assets'}:
            usable=[v for v in values if v is not None]
            if usable:
                total_key=label if key!='total' else category
                totals[total_key]=round(totals.get(total_key,0)+usable[0],4)
                if re.match(r'Others? Equity Total',label,re.I):
                    holdings.append({'Instrument':label,'Category':category,'Sector':None,'Percent to Net Assets':usable[0],
                                     'Aggregate':True,'Source Page':item['page']})
            if key=='equity total': category='Equity Futures'
            continue
        if not label: continue
        if not any(v is not None for v in values) and '^' not in cells: continue
        if style=='industry':
            weight=values[-1]
            current_sector=cells[1] or sector
            extra={}
        elif style=='sapphire':
            weight=values[2]
            current_sector=sector
            extra={'Quantity':values[0], 'Market Value (INR Lakhs)':values[1]}
            if len(values)>3 and values[3] is not None: extra['Derivatives Percent to Net Assets']=values[3]
        elif style=='redhex':
            weight=values[1]
            current_sector=cells[1] or sector
            extra={}
            if values[2] is not None: extra['Hedged Percent to Net Assets']=values[2]
            if values[3] is not None: extra['Unhedged Percent to Net Assets']=values[3]
        else:
            if style in {'rated_multi','isif'}:
                values=values[1:]
            weight=values[0]
            current_sector=sector
            extra={}
            if style in {'rated_multi','isif'}: extra['Rating']=cells[1] or None
            if style=='rated_multi' and cells[1] and not re.search(r'AAA|AA|A[123+\-]|SOV|CRISIL|ICRA|CARE|IND|FITCH|BWR',cells[1],re.I):
                label=clean(label+' '+cells[1]);extra['Rating']=None
            if len(values)>1 and values[1] is not None:
                extra['Derivatives Percent to Net Assets']=values[1]
            if len(values)>2 and values[2] is not None:
                extra['Published Net Exposure (%)']=values[2]
        if weight is None and not extra: continue
        if '^' in cells:
            extra['Published Precision Marker']='^ (below displayed precision; not zero)'
        holdings.append({'Instrument':label,'Sector':current_sector,'Category':category,
                         'Percent to Net Assets':weight,**extra,'Source Page':item['page']})
    return holdings,totals,published


def evidence(pages):
    result=[]
    for page in pages:
        result.append({'Page':page.number+1,'Text':page.get_text(),
                       'Links':[link['uri'] for link in page.get_links() if link.get('uri')]})
    return result


def nav_from_lines(page,rect):
    values={}
    for _,words in line_words(page,rect):
        texts=[w[4] for w in words]
        numbers=[number(w[4]) for w in words if number(w[4]) is not None]
        label=clean(' '.join(t for t in texts if number(t) is None)).strip('- ')
        if re.search(r'^(Growth|IDCW)\b',label,re.I) and len(numbers)==2:
            values['Regular Plan - '+label]=numbers[0]
            values['Direct Plan - '+label]=numbers[1]
        elif re.search(r'^(Regular|Direct)\s+Plan',label,re.I) and numbers:
            values[label]=numbers[-1]
    return values


def base_record(name,pages,main):
    return {'AMC / Fund House':FUND_HOUSES[name],'Portfolio Date':None,
            'Investment Objective':'','Fund Managers':[],'Fund Details':{},
            'AUM (INR Cr)':{},'NAV':{},'Base Expense Ratio (%)':{},
            'Portfolio':[],'Portfolio Totals (%)':{},'Sector Allocation (%)':{},
            'Market Capitalization (%)':{},'Asset Allocation (%)':{},
            'Published Pages':evidence(pages),'Source Pages':[page.number+1 for page in pages]}


def left_sections(text,starts):
    result={}
    for i,label in enumerate(starts):
        pattern=re.escape(label)+r'\s*(.*?)'+(re.escape(starts[i+1]) if i+1<len(starts) else r'\Z')
        match=re.search(pattern,text,re.I|re.S)
        if match: result[label]=clean(match[1])
    return result


def validate_extended(name,data,adapter,top_only=False):
    errors=[]
    warnings=[]
    checks={'recognized_fund':name in FUND_HOUSES,'portfolio_found':bool(data['Portfolio']),
            'aum_found':any(v is not None for v in data['AUM (INR Cr)'].values()),'nav_found':bool(data['NAV'])}
    for field in ['recognized_fund','portfolio_found','aum_found']:
        if not checks[field]: errors.append(field+' was not found in the published factsheet.')
    if not checks['nav_found']:
        warnings.append('NAV values are not present in the extracted factsheet text. See Published Pages and linked official NAV resource.')
    if top_only:
        warnings.append('The factsheet publishes only top positions; Portfolio is not the complete holdings list.')
    grouped=defaultdict(list)
    for holding in data['Portfolio']:
        value=holding.get('Percent to Net Assets')
        if value is not None:
            if not isinstance(value,(int,float)) or abs(value)>100:errors.append('Invalid portfolio percentage for '+holding['Instrument'])
            else:grouped[holding['Category']].append(value)
    aliases={'Equity Total':'Equity & Equity Related','Equity & Equity Related Total':'Equity & Equity Related',
             'Equity Futures Total':'Equity Futures','Corporate Bond Total':'Corporate Bond',
             'Certificate of Deposite Total':'Certificate of Deposit','Treasury Bill Total':'Treasury Bill',
             'Top 10 Equity Shares':'Equity & Equity Related'}
    subtotal_checks={}
    for label,published in data['Portfolio Totals (%)'].items():
        category=aliases.get(label,label)
        if category not in grouped or published is None:continue
        # Platinum publishes top open equity positions alongside complete debt groups.
        if top_only and category=='Equity & Equity Related' and label!='Top 10 Equity Shares':continue
        calculated=round(sum(grouped[category]),4)
        tolerance=max(.02,len(grouped[category])*.005+.005)
        okay=abs(calculated-published)<=tolerance
        subtotal_checks[label]={'Published':published,'Sum of Displayed Holdings':calculated,
                                'Rounding Tolerance':round(tolerance,4),'Within Tolerance':okay}
        if not okay:errors.append(label+' holdings do not reconcile to the published subtotal.')
    checks['portfolio_subtotals']=subtotal_checks
    if top_only and data.get('Portfolio Scope','').startswith('Top 10'):
        checks['top_ten_rows']=(len([h for h in data['Portfolio'] if h['Category']=='Equity & Equity Related'])==10
                               if 'published debt holdings' in data['Portfolio Scope'] else len(data['Portfolio'])==10)
        if not checks['top_ten_rows']:errors.append('Expected ten published top positions.')
    missing=[key for key in ['Fund Managers','Base Expense Ratio (%)'] if not data.get(key)]
    if missing: warnings.append('No numeric/text value published or verified for: '+', '.join(missing))
    data['Field Availability']={key:('extracted' if data.get(key) else 'not normalized; consult Published Pages')
                               for key in ['Investment Objective','Fund Managers','NAV','Base Expense Ratio (%)',
                                  'Sector Allocation (%)','Market Capitalization (%)','Asset Allocation (%)']}
    data['Extraction Validation']={'Status':'failed' if errors else ('passed_with_warnings' if warnings else 'passed'),
                                   'Adapter':adapter,'Checks':checks,'Errors':errors,'Warnings':warnings}


def extract_extended(document,house):
    if house=='Edelweiss Mutual Fund':
        from altiva_equity_adapter import extract_altiva_equity
        return extract_altiva_equity(document)
    if house=='Jio BlackRock Mutual Fund':
        from prism_adapter import extract_prism
        return extract_prism(document)
    if house in {'ICICI Prudential Mutual Fund','Tata Mutual Fund','Franklin Templeton Mutual Fund',
                 'Mirae Asset Mutual Fund','HSBC Mutual Fund','The Wealth Company Mutual Fund','Kotak Mahindra Mutual Fund'}:
        from new_layouts import extract_new_layouts
        return extract_new_layouts(document,house)
    pages_by_fund=defaultdict(list)
    for page in document:
        names=[name for name in detect_known_funds(page.get_text()) if FUND_HOUSES[name]==house]
        if len(names)==1: pages_by_fund[names[0]].append(page)
    funds={}
    for name,pages in pages_by_fund.items():
        if house=='quant Mutual Fund':
            main=next((p for p in pages if 'FUND SIZE' in p.get_text() and 'MONEY MANAGERS' in p.get_text()),None)
        elif house=='ITI Mutual Fund':
            main=next((p for p in pages if 'INVESTMENT OBJECTIVE' in p.get_text() and 'PORTFOLIO DETAILS' in p.get_text()),None)
        else:
            main=next((p for p in pages if re.search(r'Portfolio|TOP HOLDINGS',p.get_text(),re.I) and re.search(r'AUM|Assets Under Management',p.get_text(),re.I)),None)
        if main is None: continue
        data=base_record(name,pages,main)
        text=main.get_text()
        date=re.search(r'(?:Data (?:is )?as (?:on|of)|As on|Report As On)\s*:?\s*([^\n]+)',text,re.I)
        if date: data['Portfolio Date']=clean(date[1])
        top_only=False
        if house=='Bandhan Mutual Fund':
            left=clip_text(main,(15,68,195,810))
            data['AUM (INR Cr)']={'Month End AUM':find_number(left,r'Month end AUM\s*:\s*[^\d]*([\d,.]+)'),
                                  'Monthly Average AUM':find_number(left,r'Monthly Avg AUM\s*:\s*[^\d]*([\d,.]+)')}
            data['Fund Details']['Published Fund Details']=left_sections(left,['INVESTMENT OBJECTIVE','FUND DETAILS','NAV'])
            data['Investment Objective']=data['Fund Details']['Published Fund Details'].get('INVESTMENT OBJECTIVE','')
            manager=re.search(r'Fund Managers?\s*:(.*?)(?:\n\*|\nThis product|\Z)',left,re.I|re.S)
            if manager: data['Fund Managers']=[{'Name':clean(s),'Role':'As published'} for s in re.findall(r'Mr\.\s*([^,.\n]+)',manager[1])]
            for plan in ['Regular','Direct']:
                value=find_number(left,rf'\b{plan}\s+([\d.]+)%')
                if value is not None: data['Base Expense Ratio (%)'][plan+' Plan']=value
            for table in main.find_tables().tables:
                rows=table.extract()
                if rows and rows[0] and clean(rows[0][0])=='Plan':
                    for row in rows[1:]:
                        if number(row[-1]) is not None:
                            data['NAV'][' - '.join(clean(c) for c in row[:-1] if clean(c) not in {'','-'})]=number(row[-1])
            bottom=max(max((r.y1+2 for r in main.search_for('Grand Total')),default=514),
                       min((r.y0-3 for r in main.search_for('BY INDUSTRY')),default=514))
            rows=published_rows(main,(205,95,389,bottom),(296,353))+published_rows(main,(391,95,579,bottom),(486,544))
            holdings,totals,raw=row_holdings(rows,'industry')
            top_only=True # AMC heading is TOP HOLDINGS and includes an Others aggregate.
        elif house=='ITI Mutual Fund':
            rect=(15,95,235,813)
            left=clip_text(main,rect)
            data['AUM (INR Cr)']={'Month End AUM':field_on_line(main,'AUM (in',rect),
                                  'Monthly Average AUM':field_on_line(main,'AAUM (in',rect)}
            data['NAV']=nav_from_lines(main,rect)
            data['Investment Objective']=left_sections(left,['INVESTMENT OBJECTIVE','INVESTMENT STRATEGY DETAILS']).get('INVESTMENT OBJECTIVE','')
            data['Fund Details']['Published Investment Strategy Details']=left
            manager=re.search(r'FUND MANAGER\s*(Mr\.[^\n]+)',left,re.I)
            if manager:data['Fund Managers']=[{'Name':clean(manager[1]),'Role':'Fund Manager'}]
            rows=published_rows(main,(244,118,408,585),(343,374))+published_rows(main,(412,118,580,462),(514,548))
            holdings,totals,raw=row_holdings(rows,'multi',sectors=True)
            debt=published_rows(main,(412,472,580,505),(510,548))
            dh,dt,dr=row_holdings(debt,'industry',initial='Treasury Bill')
            holdings+=dh;totals.update(dt);raw+=dr
        elif house=='Union Mutual Fund':
            left=clip_text(main,(15,153,198,806))
            data['AUM (INR Cr)']={'Month End AUM':find_number(left,r'As on[^\n]+:\s*[^\d]*([\d,.]+)\s*crore'),
                                  'Monthly Average AUM':find_number(left,r'Average for[^\n]+:\s*[^\d]*([\d,.]+)\s*crore')}
            for plan in ['Direct','Regular']:
                nav=find_number(left,rf'\b{plan}\s*-\s*([\d.]+)')
                if nav is not None:data['NAV'][plan+' Plan']=nav
                expense=find_number(left,rf'\b{plan}\s*Plan\s*:\s*([\d.]+)%')
                if expense is not None:data['Base Expense Ratio (%)'][plan+' Plan']=expense
            data['Fund Details']['Published Investment Strategy Details']=left
            data['Investment Objective']=left_sections(left,['Investment Objective','Co-Fund Managers']).get('Investment Objective','')
            data['Fund Managers']=[{'Name':clean(m),'Role':'As published'} for m in re.findall(r'Mr\.\s*([^\n]+)',left)]
            rows=published_rows(main,(215,202,386,765),(297,329,356))+published_rows(main,(402,202,572,325),(484,518,545))
            holdings,totals,raw=row_holdings(rows,'multi',sectors=True)
        elif house=='SBI Mutual Fund':
            left=clip_text(main,(5,132,164,744))
            data['AUM (INR Cr)']={'Month End AUM':find_number(left,r'(?<!A)AUM as on[^\n]+\s*[^\d]*([\d,.]+)\s*Crores'),
                                  'Monthly Average AUM':find_number(left,r'AAUM for the Month[^\n]+\s*[^\d]*([\d,.]+)\s*Crores')}
            for _,words in line_words(main,(164,45,365,141)):
                nums=[number(w[4]) for w in words if number(w[4]) is not None]
                labels=clean(' '.join(w[4] for w in words if number(w[4]) is None))
                if re.match(r'^(Reg|Dir)-Plan-',labels) and len(nums)==1:data['NAV'][labels]=nums[0]
            data['Investment Objective']=left_sections(left,['Investment Objective','Fund Details']).get('Investment Objective','')
            data['Fund Details']['Published Fund Details']=left
            for plan in ['Regular','Direct']:
                vals=re.findall(rf'{plan}\s*:\s*([\d.]+)%',left)
                if vals:data['Base Expense Ratio (%)'][plan+' Plan']=float(vals[0])
            manager=re.search(r'Fund Managers\s*:\s*(Mr\.[^\n]+)',left,re.I)
            if manager:data['Fund Managers']=[{'Name':clean(manager[1]),'Role':'Fund Manager'}]
            rows=published_rows(main,(168,202,363,500),(253,284,310,336))+published_rows(main,(366,202,554,500),(446,485,510,536))
            holdings,totals,raw=row_holdings(rows,'rated_multi')
        elif house=='Invesco Mutual Fund':
            rect=(17,190,196,812)
            left=clip_text(main,rect)
            data['AUM (INR Cr)']={'Month End AUM':find_number(left,r'(?<!A)AuM as on\s*[^₹`]*[₹`]\s*([\d,.]+)\s*Crores'),
                                  'Monthly Average AUM':find_number(left,r'AAuM for the month\s*[^₹`]*[₹`]\s*([\d,.]+)\s*Crores')}
            for plan in ['Regular','Direct']:
                match=re.search(plan+r' Plan\s*(.*?)(?:Base Expense|Direct Plan|Benchmark|AAuM|\Z)',left,re.I|re.S)
                if match:
                    for option in ['Growth','IDCW']:
                        v=find_number(match[1],option+r'\s*([\d.]+)')
                        if v is not None:data['NAV'][plan+' Plan - '+option]=v
                v=find_number(left,rf'\b{plan}\s+([\d.]+)%')
                if v is not None:data['Base Expense Ratio (%)'][plan+' Plan']=v
            data['Fund Details']['Published Key Facts']=left
            data['Investment Objective']=left_sections(left,['Investment Objective','Key Facts']).get('Investment Objective','')
            m=re.search(r'Fund Manager & Experience\s*([^\n]+)',left,re.I)
            if m:data['Fund Managers']=[{'Name':clean(m[1]),'Role':'Fund Manager'}]
            rows=published_rows(main,(210,229,572,594),(414,528))
            holdings,totals,raw=row_holdings(rows,'industry')
        elif house=='quant Mutual Fund':
            dates=re.findall(r'Data as (?:on|of)\s*:?\s*((?:\d+\s+)?[A-Za-z]+\s+\d+,?\s+20\d{2}|\d+\s+[A-Za-z]+\s+20\d{2})',text,re.I)
            if dates:data['Portfolio Date']=dates[-1]
            elif 'September 30, 2026' in text:data['Portfolio Date']='September 30, 2026'
            data['AUM (INR Cr)']={'Published Fund Size':find_number(text,r'FUND SIZE\s*[^\d]*([\d,.]+)\s*cr')}
            left=clip_text(main,(10,138,174,835))
            data['Fund Details']['Published Investment Strategy Details']=left
            objective=left_sections(left,['INVESTMENT OBJECTIVE','RISK BAND'])
            data['Investment Objective']=objective.get('INVESTMENT OBJECTIVE','')
            managers=re.search(r'MONEY MANAGERS\s*(.*?)(?:BENCHMARK(?: INDEX)?|LOAD STRUCTURE)',left,re.I|re.S)
            if managers:data['Fund Managers']=[{'Name':clean(name),'Role':'Money Manager'} for name in managers[1].split('|')]
            starts=main.search_for('TOP 10 UNHEDGED POSITIONS')
            daily=[r for r in main.search_for('DAILY') if starts and r.y0>starts[0].y1]
            top=starts[0].y1 if starts else 263
            bottom=min(r.y0 for r in daily) if daily else 410
            rows=published_rows(main,(180,top,587,bottom),(555,))
            holdings,totals,raw=row_holdings(rows)
            for holding in holdings:
                match=re.fullmatch(r'(.*?)\s+(Equity|Debt|Invit)(?:\s+(.*))?',holding['Instrument'],re.I)
                if match:
                    holding['Instrument']=match[1]
                    holding['Asset Class']=match[2]
                    holding['Category']={'equity':'Equity & Equity Related','debt':'Debt','invit':'InvITs'}[match[2].lower()]
                    if match[3]:holding['Rating']=match[3]
            top_only=True
            data['Portfolio Scope']='Top 10 Unhedged Positions'
            data['External Field Links']=[link for page in pages for link in evidence([page])[0]['Links']]
        else:
            continue
        data['Portfolio']=holdings
        data['Portfolio Totals (%)']=totals
        data['Published Portfolio Rows']=raw
        if top_only and 'Portfolio Scope' not in data:data['Portfolio Scope']='As published (top holdings and aggregates)'
        validate_extended(name,data,house.lower().replace(' ','_')+'_coordinate_v1',top_only)
        funds[name]=data
    return funds
