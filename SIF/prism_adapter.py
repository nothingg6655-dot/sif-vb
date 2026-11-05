"""Prism's published portfolio tables, including separate futures exposure."""
import re
import pymupdf

from app import clean, normalized_name
from extended_adapters import base_record,clip_text,find_number,line_words,number,validate_extended


def table_rows(page,top,bottom):
    rows=[]
    for y,words in line_words(page,(24,top,573,bottom)):
        cells=['','','']
        for word in words:
            center=(word[0]+word[2])/2
            column=0 if center<278 else (1 if center<514 else 2)
            cells[column]+=' '+word[4]
        rows.append({'cells':[clean(c) for c in cells],'page':page.number+1,'y':round(y,2)})
    return rows


def published_aum(page,label):
    matches=page.search_for(label)
    if len(matches)!=1:raise ValueError('Prism AUM label moved or is ambiguous: '+label)
    rect=matches[0]
    region=pymupdf.Rect(rect.x0-2,rect.y1,rect.x1+10,rect.y1+17)
    values=[number(w[4]) for w in page.get_text('words')
            if region.contains(pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2)) and number(w[4]) is not None]
    # August's PDF paints 240.02 over an obsolete 76.48 at the same position.
    # The final drawing is the visible value; retain the superseded text as evidence.
    return (values[-1] if values else None),values


def extract_prism(document):
    name='Prism Hybrid Long-Short Fund'
    mains=[p for p in document if name in p.get_text() and 'Month - End AUM' in p.get_text()]
    if len(mains)!=1:raise ValueError('No unique Prism fund information page.')
    main=mains[0];text=main.get_text()
    data=base_record(name,list(document),main)
    data['Portfolio Scope']='Published holdings; futures exposure reported separately'
    date=re.search(r'Data as on ([^\n]+)',text)
    if date:data['Portfolio Date']=clean(date[1])
    revisions={}
    for field,label in [('Month End AUM','Month - End AUM'),('Monthly Average AUM','Monthly Average AUM')]:
        value,drawings=published_aum(main,label)
        data['AUM (INR Cr)'][field]=value
        if len(drawings)>1:
            revisions[field]={'Text Drawings in Paint Order':drawings,'Visible Final Value':value,
                              'Note':'Earlier text is covered by a later correction in the published PDF.'}
    if revisions:data['Published AUM Corrections']=revisions
    for plan in ['Direct','Regular']:
        data['NAV'][plan+' Plan - Growth']=find_number(text,plan+r' Plan\s*-\s*₹\s*([\d.]+)')
        data['Base Expense Ratio (%)'][plan+' Plan']=find_number(text,plan+r' Plan\s*-\s*([\d.]+)%')
    data['NAV']={k:v for k,v in data['NAV'].items() if v is not None}
    data['Base Expense Ratio (%)']={k:v for k,v in data['Base Expense Ratio (%)'].items() if v is not None}
    objective=main.search_for('The Investment Strategy aims')
    size=main.search_for('FUND SIZE')
    if objective and size:
        data['Investment Objective']=clean(clip_text(main,(40,objective[0].y0-1,285,size[0].y0-5)))
    data['Fund Managers']=[{'Name':clean(m[1]),'Role':clean(m[2])}
                          for m in re.finditer(r'(?:Mr|Ms)\.\s*([^\n]+)\n([^\n]+)\nTotal Experience',text)]
    data['Fund Details']['Published Fund Details']=text
    data['External Field Links']=['https://www.jioblackrockamc.com/prismsif/ter']
    headings={'equity equity related':'Equity & Equity Related','equity shares':'Equity & Equity Related',
              'real estate investment trust reits':'REITs','index options':'Index Options','stock options':'Stock Options',
              'non convertible debentures':'Non Convertible Debentures','commercial paper':'Commercial Paper',
              'treasury bill':'Treasury Bill','infrastructure investment trust':'InvITs'}
    section='Equity, REITs and Options';category='Equity & Equity Related'
    raw=[];holdings=[];totals={};aggregates={};futures=[];future_totals={}
    for page in document:
        body=page.get_text()
        if 'Industry Classification' not in body or '% to Net' not in body:continue
        derivative=page.search_for('DERIVATIVES')
        if derivative:
            # The main table ends at Grand Total; the lower table is exposure,
            # which must not be added to the portfolio's 100% asset weights.
            grand=page.search_for('Grand Total')
            bottom=grand[-1].y1+2 if grand else derivative[0].y0-2
        else:bottom=810
        for row in table_rows(page,96,bottom):
            label,classification,published=row['cells'];key=normalized_name(label);value=number(published)
            raw.append({'Label':label,'Values':[classification,published],'Page':row['page']})
            if key in headings:
                category=headings[key]
                if category=='InvITs':section='InvITs'
                continue
            if key in {'debt instruments','money market instruments'}:
                section=label;continue
            if value is None:continue
            if key=='sub total':totals[category]=value;continue
            if key=='total':
                aggregates[section]=value
                if category in {'Non Convertible Debentures','InvITs'}:totals[category]=value
                continue
            if key=='grand total':aggregates['Grand Total']=value;continue
            cash=key in {'treps','margin on derivatives','net receivables payables'}
            rating=bool(re.search(r'CRISIL|CARE|ICRA|SOVEREIGN|IND\b|FITCH',classification,re.I))
            holding={'Instrument':label,'Category':'Cash & Cash Equivalents' if cash else category,
                     'Sector':None if rating or cash else classification or None,
                     'Percent to Net Assets':value,'Source Page':row['page']}
            if rating:holding['Rating']=classification
            if category in {'Index Options','Stock Options'} and not cash:holding['Derivative Type']='Option (published asset value)'
            if cash:holding['Aggregate']=True
            holdings.append(holding)
        if derivative:
            kind=None
            for row in table_rows(page,derivative[0].y1+25,730):
                label,classification,published=row['cells'];key=normalized_name(label);value=number(published)
                if key in {'index future','stock future'}:kind=label;continue
                if value is None:continue
                if key in {'sub total','total'}:
                    future_totals[kind if key=='sub total' else 'Total']=value;continue
                futures.append({'Instrument':label,'Category':kind,'Sector':classification,
                                'Derivatives Percent to Net Assets':value,'Source Page':row['page']})
    data['Portfolio']=holdings;data['Portfolio Totals (%)']=totals;data['Published Portfolio Rows']=raw
    data['Published Portfolio Aggregates (%)']=aggregates
    data['Derivative Portfolio']=futures;data['Derivative Totals (%)']=future_totals
    validate_extended(name,data,'jio_blackrock_prism_v1')
    validation=data['Extraction Validation'];checks=validation['Checks'];errors=validation['Errors']
    def reconcile(label,values,published):
        actual=round(sum(values),4);tolerance=max(.02,len(values)*.005+.005)
        okay=published is not None and abs(actual-published)<=tolerance
        checks[label]={'Published':published,'Sum of Displayed Holdings':actual,'Rounding Tolerance':round(tolerance,4),'Within Tolerance':okay}
        if not okay:errors.append(label+' does not reconcile to the published total.')
    reconcile('portfolio_grand_total',[h['Percent to Net Assets'] for h in holdings],aggregates.get('Grand Total'))
    groups={'Equity, REITs and Options':{'Equity & Equity Related','REITs','Index Options','Stock Options'},
            'Debt Instruments':{'Non Convertible Debentures'},'Money Market Instruments':{'Commercial Paper','Treasury Bill'},
            'InvITs':{'InvITs'}}
    for label,categories in groups.items():
        reconcile(label,[h['Percent to Net Assets'] for h in holdings if h['Category'] in categories],aggregates.get(label))
    for kind in {h['Category'] for h in futures}:
        reconcile('derivatives_'+kind,[h['Derivatives Percent to Net Assets'] for h in futures if h['Category']==kind],future_totals.get(kind))
    reconcile('derivatives_total',[h['Derivatives Percent to Net Assets'] for h in futures],future_totals.get('Total'))
    if errors:validation['Status']='failed'
    return {name:data}
