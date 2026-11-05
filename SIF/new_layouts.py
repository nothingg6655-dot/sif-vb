"""AMC-specific metadata and published tables. No inferred or filled-in values."""
import re
from collections import defaultdict
from extended_adapters import (base_record,number,find_number,clip_text,line_words,published_rows,
                               row_holdings,validate_extended,left_sections,SECTORS)
from app import clean,detect_known_funds,normalized_name
from fund_registry import FUND_HOUSES

def label_values(page,rect):
    result={}
    for _,words in line_words(page,rect):
        nums=[number(w[4]) for w in words if number(w[4]) is not None]
        label=clean(' '.join(w[4] for w in words if number(w[4]) is None)).strip(' :₹`')
        if label and nums: result[label]=nums
    return result

def metadata(data,text):
    data['Fund Details']['Published Fund Details']=text
    for plan in ['Regular','Direct']:
        val=find_number(text,plan+r'(?:\s+Plan)?\s*:?[\s₹`]*([\d.]+)%')
        if val is not None:data['Base Expense Ratio (%)'][plan+' Plan']=val

def extract_new_layouts(document,house):
    groups=defaultdict(list)
    for page in document:
        names=[n for n in detect_known_funds(page.get_text()) if FUND_HOUSES[n]==house]
        if len(names)==1:groups[names[0]].append(page)
    result={}
    if house=='Kotak Mahindra Mutual Fund' and len(groups)==1:
        groups[next(iter(groups))]=list(document)
    for name,pages in groups.items():
        main_pattern={'Tata Mutual Fund':r'Regular Plan Growth Option',
                      'HSBC Mutual Fund':r'NAV \(as on',
                      'Mirae Asset Mutual Fund':r'Monthly Factsheet',
                      'The Wealth Company Mutual Fund':r'Month end AUM',
                      'Franklin Templeton Mutual Fund':r'Portfolio Holding Details',
                      'ICICI Prudential Mutual Fund':r'Closing AUM',
                      'Kotak Mahindra Mutual Fund':r'AAUM -'}[house]
        main=next((p for p in pages if re.search(main_pattern,p.get_text(),re.I)),None)
        if main is None:continue
        data=base_record(name,pages,main);text=main.get_text();top=False
        rows=[];holdings=[];totals={};raw=[]
        if house=='ICICI Prudential Mutual Fund':
            metadata(data,text)
            data['AUM (INR Cr)']={'Month End AUM':find_number(text,r'Closing AUM[^:]*:\s*Rs\.\s*([\d,.]+)'),
               'Monthly Average AUM':find_number(text,r'Monthly AAUM[^:]*:\s*Rs\.\s*([\d,.]+)')}
            data['NAV']={'Regular Plan - Growth':find_number(text,r'NAV \(As on[^\n]*Growth Option\s*:\s*([\d.]+)'),
                         'Direct Plan - Growth':find_number(text,r'Direct Plan - Growth\s*:\s*([\d.]+)')}
            managers=re.search(r'Fund Managers?\s*:\s*(.*?)Inception Date',text,re.S|re.I)
            if managers:data['Fund Managers']=[{'Name':clean(m),'Role':'Fund Manager'} for m in re.findall(r'([^\n()]+)\s*\(Managing',managers[1])]
            date=re.search(r'Portfolio Holding Details as on ([^\n]+)',text,re.I)
            if date:data['Portfolio Date']=clean(date[1])
            for page in pages:
                body=page.get_text()
                if 'Company/Issuer' not in body:continue
                starts=page.search_for('Company/Issuer')
                top_y=starts[0].y1+3
                page_rows=published_rows(page,(27,top_y,343,830),(203 if page!=main else 220,257,300))
                for row in page_rows:
                    cells=row['cells']
                    if cells[2] and number(cells[2]) is None and re.search(r'ICRA|CRISIL|CARE|SOV|AAA|AA[+\-]',cells[2]) and number(cells[3]) is not None:
                        cells[1:]=[cells[2],cells[3],'']
                rows+=page_rows
            holdings,totals,raw=row_holdings(rows,'isif',sectors=True)
            for label in ['Large','Mid','Small']:
                m=re.search(label+r' Cap Allocation\s*Gross\s*:\s*([\d.]+)%\s*Net\s*:\s*([\d.]+)%',text)
                if m:data['Market Capitalization (%)'][label+' Cap']={'Gross':float(m[1]),'Net':float(m[2])}
            # Shared manager/return annexures are retained separately, without attributing
            # another strategy's return series to this strategy.
            data['Shared Annexure Pages']=[{'Page':p.number+1,'Text':p.get_text()} for p in document if p.number+1 in {20,21}]
        elif house=='Tata Mutual Fund':
            left=clip_text(main,(12,92,178,487));metadata(data,left)
            data['AUM (INR Cr)']={'Month End AUM':find_number(left,r'Month End AUM[^:]*:\s*([\d,.]+)'),
                 'Monthly Average AUM':find_number(left,r'Monthly Average AUM[^:]*:\s*([\d,.]+)')}
            for plan in ['Regular','Direct']:
                data['NAV'][plan+' Plan - Growth']=find_number(left,plan+r' Plan Growth Option\s*:\s*₹?\s*([\d.]+)')
            m=re.search(r'As on ([^\n]+)',text)
            if m:data['Portfolio Date']=clean(m[1])
            data['Fund Managers']=[{'Name':clean(m),'Role':'Fund Manager'} for m in re.findall(r'Mr\.\s*(.*?)(?=\s*\d+ Years)',left,re.S)]
            data['Published Asset Allocation Rows']=published_rows(main,(183,178,404,400),(309,371))
            data['Sector Allocation (%)']={k:v[-1] for k,v in label_values(main,(183,435,398,616)).items()}
            rows=published_rows(main,(418,158,597,407),(548,))
            holdings,totals,raw=row_holdings(rows)
            top=True;data['Portfolio Scope']='Top 10 Net Equity Allocation; asset-class totals retained separately'
        elif house=='Franklin Templeton Mutual Fund':
            left=clip_text(main,(26,110,170,790));metadata(data,left)
            data['AUM (INR Cr)']={'Month End AUM':find_number(left,r'Month End\s*`\s*([\d,.]+)'),
                 'Monthly Average AUM':find_number(left,r'Monthly Average\s*`\s*([\d,.]+)')}
            for _,ws in line_words(main,(30,330,168,392)):
                nums=[number(w[4]) for w in ws if number(w[4]) is not None]
                label=clean(' '.join(w[4] for w in ws if number(w[4]) is None))
                if nums and re.search('Growth|IDCW',label):data['NAV'][('Direct' if label.startswith('Direct') else 'Regular')+' Plan - '+('Growth' if 'Growth' in label else 'IDCW')]=nums[-1]
            data['Base Expense Ratio (%)']={'Regular Plan':find_number(left,r'BASE EXPENSE RATIO\s*:?\s*([\d.]+)%'),
                'Direct Plan':find_number(left,r'BASE EXPENSE RATIO\s*\(DIRECT\)\s*:?\s*([\d.]+)%')}
            # Labels and values in this PDF are separate drawing objects.
            # Geometry pairs the expense labels to their displayed values.
            ratio=label_values(main,(30,476,166,502))
            for k,v in ratio.items():
                if 'BASE EXPENSE RATIO' in k:data['Base Expense Ratio (%)']['Direct Plan' if 'DIRECT' in k else 'Regular Plan']=v[-1]
            manager=re.search(r'FUND MANAGER\(S\)\s*([^\n]+)',left)
            if manager:data['Fund Managers']=[{'Name':clean(manager[1].split('(')[0]),'Role':'Fund Manager'}]
            obj=re.search(r'The investment strategy seeks.*?achieved\.',left,re.S|re.I)
            if obj:data['Investment Objective']=clean(obj[0])
            date=re.search(r'Portfolio Holding Details as on ([^\n]+)',text)
            if date:data['Portfolio Date']=clean(date[1])
            rows=published_rows(main,(180,146,371,607),(282,306,331,344))+published_rows(main,(376,146,565,607),(478,502,527,539))
            holdings,totals,raw=row_holdings(rows,'sapphire',sectors=True)
            holdings=[h for h in holdings if not re.search(r'^Margin on|^Call,cash',h['Instrument'])]
            # Keep the totals with their actual units, rather than treating market values as weights.
            totals={}
            for row in raw:
                if re.search(r'^Total Equity Holdings',row['Label']):
                    totals['Equity & Equity Related']=number(row['Values'][2])
        elif house=='Mirae Asset Mutual Fund':
            metadata(data,text)
            data['AUM (INR Cr)']={'Month End AUM':find_number(text,r'Net AUM \(Cr\.\).*?\n(\d[\d,.]*)\n')}
            # Fund info values are drawn after their labels. Read its fixed value region.
            vals=clip_text(main,(145,145,255,326))
            aum=re.search(r'\n(\d[\d,.]+)\n',vals)
            if aum:data['AUM (INR Cr)']['Month End AUM']=number(aum[1])
            # NAV moved down the page as subscription/redemption fields grew.
            # Anchor on the published heading rather than June's fixed rectangle.
            nav_heading=main.search_for('NET ASSET VALUE (NAV)')
            if nav_heading:
                nav_top=nav_heading[0].y1
                for _,words in line_words(main,(25,nav_top,252,nav_top+60)):
                    labels=[w[4] for w in words if w[4] in {'Growth','IDCW'}]
                    values=[number(w[4]) for w in words if number(w[4]) is not None]
                    if labels and len(values)==2:
                        data['NAV']['Direct Plan - '+labels[0]]=values[0]
                        data['NAV']['Regular Plan - '+labels[0]]=values[1]
            data['Investment Objective']=left_sections(text,['Investment objective']).get('Investment objective','')
            manager=re.search(r'Mr\. ([^\n]+)',text)
            if manager:data['Fund Managers']=[{'Name':manager[1],'Role':'Fund Manager'}]
            m=re.search(r'Monthly Factsheet as on ([^\n]+)',text)
            if m:data['Portfolio Date']=clean(m[1])
            for table in main.find_tables().tables:
                cells=table.extract()
                if cells and clean(cells[0][0])=='Instrument Name':
                    rows=[{'cells':[clean(c) for c in row],'page':main.number+1} for row in cells[1:]]
            holdings,totals,raw=row_holdings(rows,'rated_multi');top=True
            data['Portfolio Scope']='Top 10 Open Equity Positions and published debt holdings'
        elif house=='HSBC Mutual Fund':
            metadata(data,text)
            data['AUM (INR Cr)']={'Month End AUM':find_number(text,r'(?<!A)AUM \(as on[^\n]+\n₹\s*([\d,.]+)'),
                 'Monthly Average AUM':find_number(text,r'AAUM \(for the[^\n]+\n₹\s*([\d,.]+)')}
            nav=re.search(r'NAV \(.*?\)(.*?)AUM \(',text,re.S)
            if nav:
                for label,value in re.findall(r'((?:Regular|Direct) (?:Growth|IDCW))\s*([\d.]+)',nav[1]):data['NAV'][label]=number(value)
            managers=re.search(r'Name of Fund Managers\s*(.*?)Total Experience',text,re.S)
            if managers:data['Fund Managers']=[{'Name':clean(m),'Role':'Fund Manager'} for m in managers[1].split('•') if clean(m)]
            portfolio=next(p for p in pages if 'Additional Disclosure' in p.get_text() and 'Portfolio Holding Details' in p.get_text())
            m=re.search(r'Portfolio Holding Details as on ([^\n]+)',portfolio.get_text())
            if m:data['Portfolio Date']=clean(m[1])
            rows=published_rows(portfolio,(20,150,280,694),(128,197,222,250))+published_rows(portfolio,(294,150,572,366),(403,472,499,530))
            headings={'equity','corporate bonds debentures','securitised debt amort','government securities','mutual fund units','reits','invits','cash equivalent'}
            category='Equity';filtered=[]
            for row in rows:
                key=normalized_name(row['cells'][0]);v=number(row['cells'][2])
                if key in headings:
                    category=row['cells'][0];totals[category]=v
                else:
                    row['category']=category;filtered.append(row)
            holdings,_,raw=row_holdings(filtered,'redhex')
            for holding,row in zip(holdings,[r for r in filtered if r['cells'][0] and number(r['cells'][2]) is not None]):holding['Category']=row['category']
            for row in filtered:
                if normalized_name(row['cells'][0])=='net current assets':
                    holdings.append({'Instrument':'Net Current Assets','Category':'Cash Equivalent','Aggregate':True,
                                     'Percent to Net Assets':number(row['cells'][2]),'Source Page':portfolio.number+1})
            for h in holdings:
                if h['Category']!='EQUITY' and h.get('Sector'):
                    h['Rating / Classification']=h.pop('Sector')
        elif house=='The Wealth Company Mutual Fund':
            metadata(data,clip_text(main,(403,177,583,217)))
            aum=label_values(main,(223,181,395,220))
            data['AUM (INR Cr)']={('Monthly Average AUM' if 'Avg' in k else 'Month End AUM'):v[-1] for k,v in aum.items()}
            nav=label_values(main,(15,461,216,510))
            data['NAV']={k:v[-1] for k,v in nav.items() if 'Plan' in k and ('Growth' in k or 'IDCW' in k)}
            data['Fund Details']['Published Fund Features']=clip_text(main,(20,180,205,420))
            data['Investment Objective']=clip_text(main,(20,123,580,155)).strip()
            m=re.search(r'Fund Manager:\s*(.*?)(?:Entry Load)',text,re.S)
            if m:data['Fund Managers']=[{'Name':clean(m[1].split(',')[0]),'Role':'Fund Manager'}]
            m=re.search(r'Data as on ([^\n]+)',text)
            if m:data['Portfolio Date']=clean(m[1])
            rows=published_rows(main,(223,283,397,413),(359,))
            holdings,totals,raw=row_holdings(rows)
            # The first row is the aggregate of the top ten, not an eleventh holding.
            if holdings and 'Top 10' in holdings[0]['Instrument']:
                totals['Top 10 Equity Shares']=holdings.pop(0)['Percent to Net Assets']
            futures=published_rows(main,(402,283,584,346),(539,))
            data['Published Derivative and Cash Rows']=[r['cells'] for r in futures]
            data['Sector Allocation (%)']={k:v[-1] for k,v in label_values(main,(20,521,208,790)).items()}
            top=True;data['Portfolio Scope']='Top 10 Equity Shares; futures and cash published separately'
        elif house=='Kotak Mahindra Mutual Fund':
            metadata(data,text)
            data['AUM (INR Cr)']={'Month End AUM':find_number(text,r'(?<!A)AUM -\s*([\d,.]+)'),
                 'Monthly Average AUM':find_number(text,r'AAUM -\s*([\d,.]+)')}
            for label,value in re.findall(r'(Direct Growth|Direct IDCW|Growth|IDCW)\s*`([\d.]+)',clip_text(main,(320,252,580,326))):
                data['NAV'][('Direct' if label.startswith('Direct') else 'Regular')+' Plan - '+label.replace('Direct ','')]=number(value)
            objective=clip_text(main,(31,151,309,240));data['Investment Objective']=clean(objective)
            manager=re.search(r'Fund manager -\s*(.*?)AUM -',text,re.S)
            if manager:data['Fund Managers']=[{'Name':clean(s),'Role':'Fund Manager'} for s in re.findall(r'Mr\.\s*([^,&]+)',manager[1])]
            data['Portfolio Date']=re.search(r'Data as on ([^\n]+?) unless',text)[1]
            data['Asset Allocation (%)']={k:v[-1] for k,v in label_values(main,(30,517,579,620)).items() if k!='Total'}
            p=document[2];p4=document[3]
            boxes=[('Arbitrage',(25,112,291,335),(185,241),'multi'),
                   ('Directional Equity',(25,422,291,544),(238,),'single'),
                   ('REIT / InvIT',(25,652,291,723),(238,),'single'),
                   ('Relative Value Trades / Derivatives',(303,270,574,579),(512,),'single')]
            for category,rect,cuts,style in boxes:
                r=published_rows(p,rect,cuts);hs,ts,rs=row_holdings(r,style,initial=category)
                holdings+=hs;totals.update(ts);raw+=rs
            covered=published_rows(p4,(25,132,291,646),(214,253))+published_rows(p4,(303,132,574,623),(493,532))
            hs,ts,rs=row_holdings(covered,'multi',initial='Covered Call')
            holdings+=hs;totals.update(ts);raw+=rs
            data['Published Debt Category Rows']=[r['cells'] for r in published_rows(p,(303,112,574,178),(512,))]
            data['Published Other Exposure Rows']=[r['cells'] for r in published_rows(p,(303,655,574,739),(512,))]
            top=True;data['Portfolio Scope']='Published strategy positions; debt instruments shown only as category aggregates'
        else:
            continue
        data['Portfolio']=holdings;data['Portfolio Totals (%)']=totals;data['Published Portfolio Rows']=raw
        data['NAV']={k:v for k,v in data['NAV'].items() if v is not None}
        data['Base Expense Ratio (%)']={k:v for k,v in data['Base Expense Ratio (%)'].items() if v is not None}
        validate_extended(name,data,house.lower().replace(' ','_')+'_v1',top)
        result[name]=data
    return result
