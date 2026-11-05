"""Published allocation groups, kept separate from instrument holdings.

Read chart labels and printed table subtotals. Never silently infer a complete
sector split from top-ten holdings or normalize published weights to 100%.
"""
import math
import re
import unicodedata

import pymupdf

try:
    from .extended_adapters import SECTORS, number
    from .app import normalized_name
except ImportError:
    from extended_adapters import SECTORS, number
    from app import normalized_name

FIELDS = {'SECTOR': 'Sector Allocation (%)', 'ASSET': 'Asset Allocation (%)', 'RATING': 'Rating Profile (%)'}


def labeled_rows(page, rectangle, require_percent=False):
    """Match text to its printed value by row geometry, including wrapped labels."""
    rectangle = pymupdf.Rect(rectangle)
    words = [word for word in page.get_text('words') if rectangle.contains(
        pymupdf.Point((word[0] + word[2]) / 2, (word[1] + word[3]) / 2))]
    values = [word for word in words if number(word[4]) is not None
              and (not require_percent or '%' in word[4])]
    values.sort(key=lambda word: (word[1] + word[3]) / 2)
    known={normalized_name(label) for label in SECTORS | {'Chemicals'}}
    result = []
    for index, value in enumerate(values):
        y = (value[1] + value[3]) / 2
        previous = (values[index-1][1] + values[index-1][3]) / 2 if index else rectangle.y0
        following = (values[index+1][1] + values[index+1][3]) / 2 if index+1 < len(values) else rectangle.y1
        lower = (previous + y) / 2 if index else rectangle.y0
        upper = (following + y) / 2 if index+1 < len(values) else min(rectangle.y1, y + 10)
        label_words = [word for word in words if word[2] < value[0] + 1
                       and lower <= (word[1]+word[3])/2 < upper
                       and abs((word[1]+word[3])/2-y)<8
                       and number(word[4]) is None and word[4] not in {'%', ':'}]
        label = ' '.join(word[4] for word in sorted(label_words, key=lambda word: (round(word[1], 1), word[0])))
        label = unicodedata.normalize('NFKC', label).strip(' :')
        # Some two-line labels have their value on the second baseline. The
        # first line falls outside the normal row interval; join only when
        # the complete text is an explicitly known sector.
        prefix_words=[word for word in words if word[2]<value[0]
                      and 8 <= y-(word[1]+word[3])/2 < 15 and number(word[4]) is None]
        prefix=' '.join(word[4] for word in sorted(prefix_words,key=lambda word:(round(word[1],1),word[0])))
        if prefix and normalized_name(prefix+' '+label) in known:
            label=prefix+' '+label
        if label:
            result.append({'Label': label, 'Percentage': number(value[4]), 'Source Page': page.number + 1})
    # A wrapped sector heading may start between the preceding value and its
    # own value. Move only a suffix whose joined label is a known sector.
    for i in range(len(result)-1):
        words=result[i]['Label'].split()
        for split in range(1,len(words)):
            left=' '.join(words[:split]);right=' '.join(words[split:])+' '+result[i+1]['Label']
            if normalized_name(left) in known and normalized_name(right) in known:
                result[i]['Label']=left;result[i+1]['Label']=right;break
    return result


def published_group(family, title, page, rectangle, rows, basis='As published'):
    return {'Family': family, 'Title': title, 'Basis': basis, 'Source Page': page.number + 1,
            'Source Rectangle': list(rectangle) if rectangle is not None else None,
            'Scope': 'Published rows only; original denominator retained', 'Rows': rows}


def _distance(first, second):
    return math.sqrt(sum((a-b)**2 for a,b in zip(first, second)))


def pie_rows(page, rectangle):
    """Read vector pie/donut labels using legend colors and elliptical geometry."""
    region = pymupdf.Rect(rectangle)
    drawings = [d for d in page.get_drawings() if d.get('fill') and len(d['fill']) == 3
                and region.contains(d['rect']) and max(d['fill'])>.1 and min(d['fill'])<.95]
    swatches = [d for d in drawings if 2 < d['rect'].width < 13 and 2 < d['rect'].height < 13
                and .8 < d['rect'].width/d['rect'].height < 1.2]
    words = [w for w in page.get_text('words') if region.contains(pymupdf.Point((w[0]+w[2])/2, (w[1]+w[3])/2))]
    legend = []
    for swatch in swatches:
        box = swatch['rect']
        y = (box.y0 + box.y1)/2
        right = min((d['rect'].x0 for d in swatches if d['rect'].x0 > box.x1
                     and abs((d['rect'].y0+d['rect'].y1)/2-y) < 5), default=region.x1)
        matches = [w for w in words if box.x1 <= w[0] < right and abs((w[1]+w[3])/2-y) < 5
                   and number(w[4]) is None and w[4] not in {'\u25a0'}]
        label = ' '.join(w[4] for w in sorted(matches,key=lambda w:w[0]))
        if label:
            legend.append((label,swatch['fill']))
    # Legend-only charts print the percentage beside each category.
    direct = labeled_rows(page, region, require_percent=True)
    direct_lookup = {row['Label']: row for row in direct}
    if legend and all(label in direct_lookup for label,_ in legend):
        return [direct_lookup[label] for label,_ in legend]
    if len(legend) < 2:
        raise ValueError('No unambiguous allocation chart legend.')
    wedges = [d for d in drawings if d not in swatches and len(d['items']) >= 3
              and d['rect'].width > 2 and d['rect'].height > 2
              and min(_distance(d['fill'],color) for _,color in legend) < .16]
    if len(wedges) < 2:
        raise ValueError('No allocation chart wedges matching the legend.')
    bounds = pymupdf.Rect(wedges[0]['rect'])
    for drawing in wedges[1:]:bounds |= drawing['rect']
    cx,cy = (bounds.x0+bounds.x1)/2,(bounds.y0+bounds.y1)/2
    rx,ry = bounds.width/2,bounds.height/2
    pix = page.get_pixmap(matrix=pymupdf.Matrix(4,4),alpha=False)
    arc_points=[[] for _ in legend]
    for wedge in wedges:
        category=min(range(len(legend)),key=lambda i:_distance(wedge['fill'],legend[i][1]))
        for item in wedge['items']:
            if item[0]!='c':continue
            for t in (.25,.5,.75):
                p0,p1,p2,p3=item[1:]
                x=(1-t)**3*p0.x+3*(1-t)**2*t*p1.x+3*(1-t)*t*t*p2.x+t**3*p3.x
                y=(1-t)**3*p0.y+3*(1-t)**2*t*p1.y+3*(1-t)*t*t*p2.y+t**3*p3.y
                # Ignore covered portions of a background ellipse, and 3-D shadows.
                color=tuple(c/255 for c in pix.pixel(int((cx+(x-cx)*.92)*4),int((cy+(y-cy)*.92)*4))[:3])
                if _distance(color,legend[category][1])<.19:arc_points[category].append((x,y))
    labels=[w for w in words if re.fullmatch(r'[+-]?\d+(?:\.\d+)?%?',w[4])
            and .2<math.hypot(((w[0]+w[2])/2-cx)/rx,((w[1]+w[3])/2-cy)/ry)<2]
    if len(labels)!=len(legend) or any(not points for points in arc_points):
        raise ValueError(f'Allocation chart has {len(legend)} legend entries but {len(labels)} numeric labels.')
    costs=[]
    for word in labels:
        costs.append([min(math.hypot(max(word[0]-x,0,x-word[2]),max(word[1]-y,0,y-word[3]))
                          for x,y in points) for points in arc_points])
    # Assign printed labels one-to-one to colored arcs; never use slice sizes as values.
    states={0:(0,[])}
    for i in range(len(labels)):
        following={}
        for mask,(cost,path) in states.items():
            for j in range(len(legend)):
                if mask&(1<<j):continue
                key=mask|(1<<j);candidate=cost+costs[i][j]
                if key not in following or candidate<following[key][0]:following[key]=(candidate,path+[j])
        states=following
    _,assignment=states[(1<<len(legend))-1]
    result={legend[j][0]:{'Label':legend[j][0],'Percentage':number(word[4]),'Source Page':page.number+1}
            for word,j in zip(labels,assignment)}
    return [result[label] for label,_ in legend]


def _add(groups,family,title,page,rect,require_percent=False,basis='As published'):
    rows=labeled_rows(page,rect,require_percent)
    if rows:groups.append(published_group(family,title,page,rect,rows,basis))


def extract_allocation_groups(document,data):
    house=data['AMC / Fund House']
    pages=[document[n-1] for n in data['Source Pages'] if isinstance(n,int)]
    groups=[]
    for page in pages:
        if house=='Franklin Templeton Mutual Fund':
            headings=page.search_for('Industry Allocation - Equity Assets')
            for heading in headings:
                _add(groups,'SECTOR','Industry Allocation - Equity Assets',page,(176,heading.y1,374,810),True)
            # The asset totals are separately published at the foot of the holdings table.
            if headings:
                asset_rows=[]
                for term in ['Total Holdings','Margin on Derivatives','Call,cash and other current asset']:
                    for label in page.search_for(term):
                        if label.y0>=headings[0].y0:continue
                        rows=labeled_rows(page,(label.x0-1,label.y0-1,566,label.y1+1))
                        # Two numeric columns are market value and % assets. Keep the latter.
                        if rows:asset_rows.append({'Label':term,'Percentage':rows[-1]['Percentage'],'Source Page':page.number+1})
                if asset_rows:groups.append(published_group('ASSET','Published portfolio asset totals',page,None,asset_rows))
        elif house=='Bandhan Mutual Fund':
            for heading in page.search_for('INDUSTRY ALLOCATION (%)'):
                if heading.y0 < 500:rects=[(205,heading.y1+1,580,690)]
                else:rects=[(205,heading.y1+1,310,695),(310,heading.y1+1,422,695)]
                rows=[row for rect in rects for row in labeled_rows(page,rect,True)]
                if rows:groups.append(published_group('SECTOR','Industry Allocation (%)',page,None,rows))
            if page.search_for('ASSET CLASS / RATING CLASS (%)'):
                rows=[]
                words=page.get_text('words')
                for label in ['AAA Equivalent','AA+','AA']:
                    for box in page.search_for(label):
                        if box.x0<440 or box.y0<550:continue
                        if label=='AA' and any(w[4]!='AA' for w in words if pymupdf.Rect(w[:4]).intersects(box)):continue
                        matches=[w for w in words if '%' in w[4] and number(w[4]) is not None
                                 and 0<w[1]-box.y0<12 and abs((w[0]+w[2]-box.x0-box.x1)/2)<15]
                        if len(matches)==1:rows.append({'Label':label,'Percentage':number(matches[0][4]),'Source Page':page.number+1})
                if rows:groups.append(published_group('RATING','Composition by Rating',page,None,rows))
        elif house=='SBI Mutual Fund':
            for heading in page.search_for('INDUSTRY ALLOCATION (%)'):
                _add(groups,'SECTOR','Industry Allocation (%)',page,(168,heading.y1,365,665))
                rows=pie_rows(page,(365,538,556,665))
                groups.append(published_group('MIXED_ASSET_RATING','Asset Class / Rating Class',page,(365,538,556,665),rows))
        elif house=='Invesco Mutual Fund':
            headings=page.search_for('Industry Classification')
            for heading in headings:_add(groups,'SECTOR','Industry Classification',page,(207,heading.y1,405,805),True)
            if headings:
                # These two percentages are explicitly printed in the portfolio table.
                rows=[{'Label':key,'Percentage':value,'Source Page':page.number+1}
                      for key,value in data['Portfolio Totals (%)'].items() if key!='Grand Total']
                groups.append(published_group('ASSET','Asset Allocation',page,None,rows))
        elif house=='The Wealth Company Mutual Fund':
            for heading in page.search_for('Sector Holdings'):
                _add(groups,'SECTOR','Sector Holdings',page,(17,heading.y1,217,790),True)
            rows=[]
            for cells in data.get('Published Derivative and Cash Rows',[]):
                if 'Cash' in cells[0] and number(cells[-1]) is not None:
                    rows.append({'Label':cells[0],'Percentage':number(cells[-1]),'Source Page':page.number+1})
            if rows:groups.append(published_group('ASSET','Published cash allocation',page,None,rows,'Published cash subtotal; equity total not separately printed'))
        elif house=='HSBC Mutual Fund':
            for heading in page.search_for('Portfolio Summary as on'):
                _add(groups,'ASSET','Portfolio Summary',page,(450,heading.y1,579,565),True)
            for heading in page.search_for('Rating Allocation for Debt Holding'):
                _add(groups,'RATING','Rating Allocation for Debt Holding',page,(447,heading.y1,580,690),True)
        elif house=='Tata Mutual Fund':
            for heading in page.search_for('COMPOSITION BY RATINGS/MARKET CAP'):
                _add(groups,'RATING','Composition by Ratings',page,(335,heading.y1,575,heading.y1+90),True)
            if 'Industry Allocation (%)' in page.get_text():
                rows=[]
                for item in data.get('Published Asset Allocation Rows',[]):
                    if item.get('page')!=page.number+1:continue
                    cells=item['cells']
                    if cells[0] in {'Net Assets','Portfolio Total'}:continue
                    value=number(cells[-1])
                    if value is not None:rows.append({'Label':cells[0],'Percentage':value,'Source Page':page.number+1})
                if rows:groups.append(published_group('ASSET','Published Asset Allocation',page,None,rows))
        elif house=='ITI Mutual Fund':
            if page.search_for('Portfolio Allocation of other Asset Class'):
                _add(groups,'ASSET','Portfolio Classification by Net Assets',page,(238,754,352,803))
                _add(groups,'ASSET','Portfolio Allocation of other Asset Class',page,(353,754,470,803))
                values=sorted([w for w in page.get_text('words') if 285<w[0]<560 and 575<w[1]<650
                               and re.fullmatch(r'\d+\.\d+',w[4])],key=lambda w:w[0])
                if len(values)!=20:raise ValueError('Expected ten paired ITI sector/benchmark labels.')
                lines=[line for block in page.get_text('dict',clip=pymupdf.Rect(285,650,560,735))['blocks']
                       for line in block.get('lines',[]) if line['dir']==(0.,-1.)]
                rows=[[],[]]
                for i in range(10):
                    center=(values[2*i][0]+values[2*i+1][2])/2
                    parts=sorted([line for line in lines if abs((line['bbox'][0]+line['bbox'][2])/2-center)<11],key=lambda line:line['bbox'][0])
                    label=' '.join(span['text'].strip() for line in parts for span in line['spans'])
                    if not label:raise ValueError('Missing rotated ITI sector label.')
                    for offset in (0,1):rows[offset].append({'Label':label,'Percentage':number(values[2*i+offset][4]),'Source Page':page.number+1})
                groups.append(published_group('SECTOR','Sector Allocation vs Benchmark',page,(285,575,560,735),rows[0],'Net scheme exposure (% NAV)'))
                groups.append(published_group('BENCHMARK_SECTOR','Sector Allocation vs Benchmark',page,(285,575,560,735),rows[1],'Nifty 50 (TRI) Index'))
        elif house=='ICICI Prudential Mutual Fund':
            for heading in page.search_for('Asset Allocation Details (%)'):
                rect=(362,heading.y1,562,heading.y1+119)
                rows=pie_rows(page,rect)
                groups.append(published_group('ASSET','Asset Allocation Details (%)',page,rect,rows,'Published gross asset and derivative exposure'))
            for heading in page.search_for('Rating Allocation for Debt Holdings (%)'):
                _add(groups,'RATING','Rating Allocation for Debt Holdings (%)',page,(357,heading.y1,565,heading.y1+65),True)
        elif house=='Mirae Asset Mutual Fund':
            for heading in page.search_for('OVERALL ASSET ALLOCATION'):
                rect=(260,heading.y1,570,326)
                rows=pie_rows(page,rect)
                for row in rows:
                    if row['Label']=='Cash, Cash Equivalents,':row['Label']='Cash, Cash Equivalents, Derivative Margin And Others'
                groups.append(published_group('ASSET','Overall Asset Allocation',page,rect,rows))
        elif house=='quant Mutual Fund':
            from .raster_allocations import relative_weights,asset_allocation
            groups.extend(relative_weights(page));groups.extend(asset_allocation(page))
        elif house=='Edelweiss Mutual Fund':
            # The Hybrid strategy publishes a strategy allocation table, not a sector split.
            for heading in page.search_for('Portfolio summary as on'):
                if page.search_for('Allocation (%)'):
                    rows=[r for r in labeled_rows(page,(16,heading.y1+40,575,790),True)
                          if normalized_name(r['Label'])!='total']
                    if rows:groups.append(published_group('ASSET','Portfolio strategy allocation',page,None,rows))
            if 'equity-ex-top-100' in str(data.get('Factsheet Source',{}).get('Local PDF','')).lower() or page.search_for('Top 10 Sectors (%)'):
                for heading in page.search_for('Top 10 Sectors (%)'):
                    _add(groups,'SECTOR_TOP_TEN','Top 10 Sectors (%)',page,(15,heading.y1,345,800))

    # Printed sector subtotal rows are more complete/precise than top-five charts.
    if house in {'ICICI Prudential Mutual Fund','Edelweiss Mutual Fund'}:
        sector_keys={normalized_name(key) for key in SECTORS | {'Chemicals'}}
        rows=[];derivatives=[]
        for row in data.get('Published Portfolio Rows',[]):
            if normalized_name(row.get('Label','')) not in sector_keys:continue
            values=row.get('Values',[])
            value=number(values[1]) if house=='ICICI Prudential Mutual Fund' and len(values)>1 else number(values[-1]) if values else None
            if value is not None:rows.append({'Label':row['Label'],'Percentage':value,'Source Page':row['Page']})
            if house=='ICICI Prudential Mutual Fund' and len(values)>2 and number(values[2]) is not None:
                derivatives.append({'Label':row['Label'],'Percentage':number(values[2]),'Source Page':row['Page']})
        if rows:groups.append({'Family':'SECTOR','Title':'Published sector subtotals','Basis':'Cash securities (% NAV)',
                               'Rows':rows,'Scope':'Published sector subtotals; not summed from holdings'})
        if derivatives:groups.append({'Family':'SECTOR_DERIVATIVE','Title':'Published sector derivative exposure',
                                      'Basis':'Derivative exposure (% NAV)','Rows':derivatives})
    if house=='ITI Mutual Fund':
        # These are printed cash-sector and derivative-sector subtotals. Keep
        # both columns; the separate net comparison chart is retained below.
        keys={normalized_name(s) for s in SECTORS}
        for offset,family,basis in [(0,'SECTOR_CASH','Cash securities (% NAV)'),(1,'SECTOR_DERIVATIVE','Derivative exposure (% NAV)')]:
            rows=[{'Label':r['Label'],'Percentage':number(r['Values'][offset]),'Source Page':r['Page']}
                  for r in data.get('Published Portfolio Rows',[]) if normalized_name(r.get('Label','')) in keys
                  and len(r.get('Values',[]))>offset and number(r['Values'][offset]) is not None]
            if rows:groups.append({'Family':family,'Title':'Published sector subtotals','Basis':basis,'Rows':rows})
    # Published portfolio class totals are useful even when there is no pie
    # chart. Exclude grand totals and notional futures from cash asset weights.
    if not any(g['Family']=='ASSET' for g in groups) and house in {
            'Bandhan Mutual Fund','SBI Mutual Fund','Jio BlackRock Mutual Fund','The Wealth Company Mutual Fund',
            'Edelweiss Mutual Fund','Franklin Templeton Mutual Fund'}:
        source=data.get('Portfolio Summary (%)') or data.get('Portfolio Totals (%)') or {}
        rows=[{'Label':label,'Percentage':value,'Source Page':None} for label,value in source.items()
              if value is not None and normalized_name(label) not in {'grand total','total','total net assets','top 10 equity shares'}
              and not re.search(r'Futures|Others? Equity Total',label,re.I)]
        if house=='Jio BlackRock Mutual Fund':
            rows.extend({'Label':h['Instrument'],'Percentage':h['Percent to Net Assets'],'Source Page':h.get('Source Page')}
                        for h in data['Portfolio'] if h.get('Aggregate') and h['Category']=='Cash & Cash Equivalents')
        if rows:groups.append({'Family':'ASSET','Title':'Published portfolio category totals','Basis':'Published table subtotals',
                               'Scope':'Published categories; derivative exposure shown separately','Rows':rows})
    return groups


def enrich_allocations(document,funds):
    for name,data in funds.items():
        try:
            groups=extract_allocation_groups(document,data)
        except (ValueError, RuntimeError) as error:
            validation=data.setdefault('Extraction Validation',{})
            validation['Status']='passed_with_warnings'
            validation.setdefault('Warnings',[]).append('Allocation extraction failed: '+str(error))
            for field in FIELDS.values():data.setdefault('Field Availability',{})[field]='PARSE_FAILED'
            data.setdefault('Supplementary Fields',{})['Allocation Extraction Error']=str(error)
            continue
        for family,field in FIELDS.items():
            selected=[group for group in groups if group['Family']==family]
            if selected:
                values={}
                for group in selected:
                    for row in group['Rows']:
                        label=row['Label']
                        if label in values and values[label]!=row['Percentage']:
                            raise ValueError(f'Conflicting published {family} allocation for {name}: {label}')
                        values[label]=row['Percentage']
                data[field]=values
            elif data.get(field):
                groups.append({'Family':family,'Title':field,'Basis':'Published labels from verified factsheet adapter',
                               'Source Pages':data['Source Pages'],
                               'Rows':[{'Label':label,'Percentage':value} for label,value in data[field].items()]})
            status='PUBLISHED' if data.get(field) else 'NOT_PUBLISHED'
            data.setdefault('Field Availability',{})[field]=status
        data.setdefault('Supplementary Fields',{})['Published Allocation Groups']=groups
    return funds
