"""Hourly, official-only discovery and per-scheme factsheet refresh for every AMC."""
import argparse
import hashlib
import html
import json
import logging
import re
import sys
import time
import threading
import tempfile
import copy
import calendar
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote,urljoin,urlsplit

import pymupdf
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from .app import extract_factsheet, detect_known_funds, normalized_name
    from .automation import filename, reporting_period, write_json, atomic_write, output_lock, utc_now, MAX_BYTES
    from .fund_registry import FUND_HOUSES
except (ImportError, ValueError):
    from app import extract_factsheet, detect_known_funds, normalized_name
    from automation import filename, reporting_period, write_json, atomic_write, output_lock, utc_now, MAX_BYTES
    from fund_registry import FUND_HOUSES

ROOT=Path(__file__).resolve().parent
LOG=logging.getLogger('sif-monitor')
PDF_LOCK=threading.Lock()  # PyMuPDF document operations are not thread-safe.

def extractor_version():
    return hashlib.sha256(b''.join((ROOT/p).read_bytes() for p in ['app.py','extended_adapters.py','new_layouts.py','prism_adapter.py','altiva_equity_adapter.py','arthaya_portfolio.py','market_cap.py','allocations.py','raster_allocations.py','reviewed_raster_allocations.json','risk_bands.py','reviewed_risk_bands.json','windows_ocr.ps1','output_schema.py','fund_registry.py'])).hexdigest()

def allows(profile,url):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.username or parsed.password:return False
    path=unquote(parsed.path)
    if any(p in {'.','..'} for p in path.split('/')):return False
    for prefix in profile['allowed_prefixes']:
        allowed=urlsplit(prefix)
        if parsed.netloc.lower()==allowed.netloc.lower() and path.startswith(unquote(allowed.path)):return True
    return False

def pdf_links(markup,base):
    text=html.unescape(markup).replace(r'\/', '/').replace(r'\"','"').replace(r'\u002F','/').replace(r'\u0026','&')
    soup=BeautifulSoup(text,'html.parser')
    if soup.find('base',href=True):base=urljoin(base,soup.find('base')['href'])
    links=[a.get('href') for a in soup.find_all('a',href=True) if '.pdf' in a['href'].lower()]
    quoted=re.findall(r'''["']([^<>"'\\\r\n]*?\.pdf(?:\?[^<>"'\\\r\n]*)?)["']''',text,re.I)
    # A JSON file_name can end in .pdf without being a URL. Bare filenames are
    # accepted only from actual HTML href attributes, which have a known base.
    links += [x for x in quoted if '/' in x]
    links+=re.findall(r'''https://[^<>"'\\\r\n]+?\.pdf(?:\?[^<>"'\\\s]*)?''',text,re.I)
    return list(dict.fromkeys(urljoin(base,x.strip()) for x in links if x and len(x)<1000))

def walk(value):
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from walk(child)
    elif isinstance(value,list):
        for child in value:yield from walk(child)
    elif isinstance(value,str) and value[:1] in '[{':
        try:yield from walk(json.loads(value))
        except ValueError:pass

def prism_periods(now=None,months=12):
    """Search month filters newest first, including the fiscal-year boundary."""
    now=now or datetime.now()
    current=now.year*12+now.month-1
    for offset in range(months):
        year,month=divmod(current-offset,12);month+=1
        fiscal=year if month>=4 else year-1
        yield f'{fiscal}-{fiscal+1}',calendar.month_name[month]

class Publisher:
    def __init__(self,profile,browser=True):
        self.profile=profile;self.browser=browser
        self.session=requests.Session()
        self.session.headers.update({'User-Agent':'Mozilla/5.0','Accept':'text/html,application/json,*/*'})
        retry=Retry(total=1,backoff_factor=.4,status_forcelist=[429,500,502,503,504],allowed_methods=['GET','POST'])
        self.session.mount('https://',HTTPAdapter(max_retries=retry))

    def request(self,url,method='GET',body=None,referer=None):
        if not allows(self.profile,url):raise ValueError('URL outside configured official prefixes: '+url)
        headers={'Referer':referer or self.profile['pages'][0]}
        # Inspect every redirect before following it; a remote page cannot authorize
        # a mirror, an HTTP downgrade, or an unrelated host.
        for _ in range(6):
            response=self.session.request(method,url,json=body,headers=headers,timeout=(12,35),allow_redirects=False,stream=True)
            if response.is_redirect:
                target=urljoin(url,response.headers['Location']);response.close()
                if not allows(self.profile,target):raise ValueError('Official URL redirected outside allowlist')
                url=target
                if response.status_code==303:method='GET';body=None
                continue
            try:
                response.raise_for_status()
                if int(response.headers.get('Content-Length','0'))>MAX_BYTES:raise ValueError('Document exceeds size limit')
                data=bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data)>MAX_BYTES:raise ValueError('Response exceeds size limit')
                return bytes(data)
            finally:response.close()
        raise ValueError('Too many redirects')

    def get_json(self,url,method='GET',body=None):return json.loads(self.request(url,method,body))

    def discover(self):
        p=self.profile;strategy=p['strategy'];items=[];errors=[]
        def add(url,page=None):
            if allows(p,url) and re.search(p['pdf_pattern'],unquote(url),re.I):
                items.append({'url':url,'page_url':page or p['pages'][0]})
        if strategy=='icici':
            categories=self.get_json(p['api_categories'])
            category=next(v for v in walk(categories) if 'fact' in v.get('internalName','').lower() and v.get('id'))
            category_id=category['id']
            payload={'categoryId':category_id,'schemeCategory':'','userType':'Investor','fileType':'All',
                     'page':'1','size':'20','filter':[],'categoryName':'factsheets'}
            files=self.get_json(p['api_files'],'POST',payload)
            for item in walk(files):
                if item.get('url') and '.pdf' in item['url'].lower():add(urljoin('https://www.icicipruamc.com/blob/',item['url'].lstrip('/')))
        elif strategy=='kotak':
            catalog=self.get_json(p['api_catalog'])
            sheets=[v for v in catalog.get('superHeaderList',[]) if 'factsheet' in v.get('superHeaderTitle','').lower()]
            for section in sheets:
                for header in section.get('headerList',[]):
                    year=max(header.get('yearList',[]),default=None)
                    months=header.get('monthList',[])
                    if year and months:
                        month=max(months)
                        url=p['api_base']+f"/getsubheaderList/{header['headerId']}?year={year}&month={month}&type=month"
                        body=self.get_json(url)
                        for item in body.get('subHeaderList',[]):
                            content=item.get('content','')
                            if content:add(urljoin(p['document_base'],content))
                    elif header.get('content'):add(urljoin(p['document_base'],header['content']))
        elif strategy=='sapphire':
            body=self.get_json(p['api'])
            for item in walk(body):
                href=item.get('literatureHref','')
                if href and ('FACTSHEET' in item.get('id','').upper() or 'factsheet' in href.lower()):
                    add(urljoin('https://www.franklintempletonindia.com/download/',href.lstrip('/')))
        elif strategy=='platinum':
            # The fund page has a stale static factsheet; use the same paginated
            # catalog as Forms & Downloads instead of stopping at that old link.
            from urllib.parse import parse_qsl,urlencode
            catalog_url=p['api']
            for page_no in range(1,21):
                parsed=urlsplit(catalog_url);outer=dict(parse_qsl(parsed.query))
                endpoint=urlsplit(outer['endpoint']);query=dict(parse_qsl(endpoint.query))
                query['pageNo']=str(page_no)
                outer['endpoint']=endpoint.path+'?'+urlencode(query)
                request_url=parsed._replace(query=urlencode(outer)).geturl()
                body=self.get_json(request_url)
                for item in walk(body):
                    if item.get('link') and '.pdf' in item['link'].lower():add(item['link'])
                pages=max((int(v.get('totalPages',1)) for v in walk(body) if 'totalPages' in v),default=1)
                if page_no>=pages:break
            else:raise ValueError('Platinum catalog exceeded the page limit; latest listing is unverified.')
        elif strategy in {'tata','summit','sbi','altiva'}:
            response=self.request(p['api'],p.get('method','GET'),p.get('body'))
            for url in pdf_links(response.decode('utf-8',errors='replace'),p['pages'][0]):add(url)
        else:
            for page_url in p['pages']:
                try:
                    markup=self.request(page_url).decode('utf-8',errors='replace')
                    if 'Request Rejected' in markup:raise ValueError('Official server rejected the request')
                    for url in pdf_links(markup,page_url):add(url,page_url)
                    if not items and p.get('read_scripts'):
                        soup=BeautifulSoup(markup,'html.parser')
                        for script in soup.select('script[src]'):
                            url=urljoin(page_url,script['src'])
                            if not allows(p,url):continue
                            script_text=self.request(url).decode('utf-8',errors='replace')
                            for pdf in pdf_links(script_text,page_url):add(pdf,page_url)
                except Exception as e:errors.append(str(e))
            if not items and self.browser and p.get('browser_discovery'):
                try:
                    for item in self.discover_browser():add(item['url'],item['page_url'])
                except Exception as e:errors.append(str(e))
        if not items:
            raise ValueError('; '.join(errors) or 'The official factsheet listing returned no downloadable factsheet links.')
        return list({i['url']:i for i in items}.values())

    def discover_browser(self):
        from playwright.sync_api import sync_playwright
        result=[]
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for url in self.profile['pages']:
                    page=browser.new_page()
                    def capture(response):
                        if response.request.resource_type not in {'xhr','fetch'} or not allows(self.profile,response.url):return
                        if not re.search(r'factsheet|literature|downloads|files\?',response.url,re.I):return
                        try:
                            for pdf in pdf_links(response.text(),url):result.append({'url':pdf,'page_url':url})
                        except Exception:pass
                    page.on('response',capture)
                    page.goto(url,wait_until='domcontentloaded',timeout=45000)
                    page.wait_for_timeout(7000)
                    # Inspect published month options. Do not solve or bypass bot challenges.
                    if self.profile['id']=='prism':
                        result+=self.discover_prism_months(page,url)
                    for pdf in pdf_links(page.content(),url):result.append({'url':pdf,'page_url':url})
                    page.close()
            finally:browser.close()
        return result

    def discover_prism_months(self,page,url):
        """The default and previous months may both be empty (e.g. October/September)."""
        def documents():
            return [{'url':pdf,'page_url':url} for pdf in pdf_links(page.content(),url)
                    if allows(self.profile,pdf) and re.search(self.profile['pdf_pattern'],unquote(pdf),re.I)]
        for fiscal,month in prism_periods():
            selectors=page.locator('.ant-select-selector')
            if selectors.count()<2:break
            for index,label in [(0,fiscal),(1,month)]:
                if selectors.nth(index).inner_text().strip()==label:continue
                selectors.nth(index).click()
                page.locator('.ant-select-dropdown:visible').wait_for(state='visible',timeout=5000)
                option=page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter(has_text=re.compile('^'+re.escape(label)+'$'))
                if not option.count():
                    page.keyboard.press('Escape')
                    # No older fiscal year is offered by the publisher.
                    if index==0:return []
                    break
                option.first.click();page.wait_for_timeout(3000)
            else:
                found=documents()
                if found:return found
        return []

def document_group(url):
    name=normalized_name(unquote(urlsplit(url).path.rsplit('/',1)[-1]))
    if 'ex top' in name or 'extop' in name or 'ex top100' in name:return 'equity-ex-top-100'
    if 'hybrid' in name:return 'hybrid'
    if 'active asset' in name:return 'active-asset-allocator'
    if 'sector rotation' in name:return 'sector-rotation'
    if 'equity' in name:return 'equity'
    return 'combined'

def latest_candidates(items):
    groups={}
    for item in items:groups.setdefault(document_group(item['url']),[]).append(item)
    result=[]
    for group in groups.values():
        newest=max((reporting_period(i['url']) or '' for i in group),default='')
        result.extend(i for i in group if not reporting_period(i['url']) or reporting_period(i['url'])==newest)
    if len(result)>18:raise ValueError('Too many undated factsheets; discovery profile needs review.')
    return result

def fund_period(data,pdf_text):
    value=data.get('Portfolio Date') or ''
    period=reporting_period(value)
    numeric=re.search(r'\b\d{1,2}[/.](\d{1,2})[/.](20\d{2})\b',value)
    if numeric:period=f'{numeric[2]}-{int(numeric[1]):02d}'
    if period:return period
    # Prefer portfolio headings to cover/publication/upload months.
    patterns=[r'Portfolio(?: Holding Details| Summary| Holdings)?\s+(?:as on|as at)\s*([^\n]+)',
              r'Monthly Factsheet as on\s*([^\n]+)',r'(?:Data as on|Data as of|As on)\s*:?[ \t]*([^\n]+)']
    for pattern in patterns:
        periods={reporting_period(m[1]) for m in re.finditer(pattern,pdf_text,re.I)}-{None}
        if len(periods)==1:return periods.pop()
    raise ValueError('No unambiguous portfolio reporting month verified in PDF')

def merge_editions(old,data):
    if old is None:return data
    old_source=old['Factsheet Source'];new_source=data['Factsheet Source']
    same_month=old_source['Reporting Month']==new_source['Reporting Month']
    regular_pair=('_Regular_' in old_source['PDF URL'] or '_Regular_' in new_source['PDF URL']) and old_source['PDF URL']!=new_source['PDF URL']
    if same_month and regular_pair:
        primary,extra=(old,data) if '_Regular_' in new_source['PDF URL'] else (data,old)
        editions=primary.get('Additional Factsheet Editions',[])
        if extra['Factsheet Source']['SHA256'] not in {e['Factsheet Source']['SHA256'] for e in editions}:
            editions.append({'Factsheet Source':extra['Factsheet Source'],
                'Scheme Performance':extra.get('Scheme Performance',[]),
                'Risk Band & Strategy':extra.get('Risk Band & Strategy',{}),
                'Published Pages':extra.get('Published Pages',[])})
        primary['Additional Factsheet Editions']=editions
        return primary
    return data if new_source['Reporting Month']>=old_source['Reporting Month'] else old

def load_profiles(path):
    config=json.loads(Path(path).read_text(encoding='utf-8'))
    profiles=config['sources']
    if len({p['id'] for p in profiles})!=len(profiles):raise ValueError('Duplicate publisher ID')
    if len({p['fund_house'] for p in profiles})!=len(profiles):raise ValueError('Duplicate fund house')
    for p in profiles:
        if p['fund_house'] not in FUND_HOUSES.values():raise ValueError('Unknown fund house')
        if not all(allows(p,url) for url in p['pages']):raise ValueError('Publishing page not allowlisted')
        if not all(allows(p,i['url']) for i in p.get('known_documents',[])):raise ValueError('Seed PDF not official')
        re.compile(p['pdf_pattern'])
    return profiles

def load_saved_schemes(profile,directory,previous):
    """Load last-good scheme files only when their recorded checksums match."""
    funds={};house=profile['fund_house'];folder=(Path(directory)/'latest/extraction').resolve()
    for name,record in previous.get('scheme_files',{}).items():
        path=Path(record['path'])
        if path.resolve().parent!=folder:
            # State files contain absolute paths. Rebase a checksum-matching file
            # after the project directory is moved or renamed.
            path=folder/Path(record['path']).name
        if path.resolve().parent!=folder or FUND_HOUSES.get(name)!=house:continue
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:continue
        saved=json.loads(path.read_text(encoding='utf-8')).get(name)
        if saved and saved.get('Extraction Validation',{}).get('Status') in {'passed','passed_with_warnings'}:
            source=saved.get('Factsheet Source',{})
            local=Path(source.get('Local PDF',''))
            if source.get('SHA256') and not local.is_file():
                for candidate in folder.parent.glob('*.pdf'):
                    if hashlib.sha256(candidate.read_bytes()).hexdigest()==source['SHA256']:
                        source['Local PDF']=str(candidate.resolve());break
            funds[name]=saved
    # One-time compatibility with outputs produced before the folder migration.
    legacy=Path(directory)/(filename(house)+'.json')
    if not previous.get('scheme_files') and legacy.is_file():
        if not previous.get('json_sha256') or hashlib.sha256(legacy.read_bytes()).hexdigest()==previous['json_sha256']:
            funds={n:d for n,d in json.loads(legacy.read_text(encoding='utf-8')).items()
                   if FUND_HOUSES.get(n)==house and d.get('Extraction Validation',{}).get('Status') in {'passed','passed_with_warnings'}}
    return funds

def save_scheme_files(directory,funds):
    try:
        from .output_schema import normalize_fund_record
    except (ImportError, ValueError):
        from output_schema import normalize_fund_record
    folder=Path(directory)/'latest/extraction';folder.mkdir(parents=True,exist_ok=True)
    if folder.resolve().parent!=(Path(directory)/'latest').resolve():raise ValueError('Extraction folder outside latest output')
    records={}
    for name,data in funds.items():
        data=normalize_fund_record(data)
        path=folder/(filename(name)+'.json');write_json(path,{name:data})
        records[name]={'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    return records

def save_house_pdfs(directory,house,funds):
    """Replace current primary PDFs after verifying all selected source bytes."""
    latest=Path(directory)/'latest';latest.mkdir(parents=True,exist_ok=True)
    if latest.resolve().parent!=Path(directory).resolve():raise ValueError('Latest folder outside output directory')
    groups={}
    for data in funds.values():
        source=data.get('Factsheet Source',{})
        if source.get('SHA256') and source.get('Local PDF'):
            groups.setdefault(source['SHA256'],{'source':source,'months':set()})['months'].add(source['Reporting Month'])
    prepared=[]
    for digest,group in groups.items():
        content=Path(group['source']['Local PDF']).read_bytes()
        if hashlib.sha256(content).hexdigest()!=digest:raise ValueError('PDF checksum mismatch for '+house)
        stem=filename(house)
        if len(groups)>1:stem+=' - '+document_group(group['source']['PDF URL'])+' - '+'_'.join(sorted(group['months']))+' - '+digest[:8]
        prepared.append((digest,latest/(stem+'.pdf'),content))
    paths={}
    for digest,path,content in prepared:
        atomic_write(path,content);paths[digest]=str(path.resolve())
    for data in funds.values():
        source=data.get('Factsheet Source',{})
        if source.get('SHA256') in paths:source['Local PDF']=paths[source['SHA256']]
        # Alternate plan evidence remains in JSON; only primary PDFs are retained.
        for edition in data.get('Additional Factsheet Editions',[]):edition['Factsheet Source'].pop('Local PDF',None)
    return paths

def refresh_house(profile,directory,previous,browser=True,offline=False):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.sif-refresh-',dir=directory) as staging:
        return _refresh_house(profile,directory,previous,browser,offline,Path(staging))

def _refresh_house(profile,directory,previous,browser,offline,staging):
    publisher=Publisher(profile,browser)
    now=utc_now();house=profile['fund_house'];version=extractor_version()
    saved=load_saved_schemes(profile,directory,previous)
    errors=[];discovered=[];listing_verified=False
    if not offline:
        try:discovered=latest_candidates(publisher.discover());listing_verified=True
        except Exception as e:errors.append('Discovery: '+str(e))
    known=profile.get('known_documents',[])
    items=list({i['url']:i for i in known+discovered}.values())
    # Process only the newest known edition of each scheme group plus current links.
    items=latest_candidates(items)
    funds={};documents=[];failed_funds={}
    for item in items:
        try:
            if offline:
                path=ROOT/item['local_path'];content=path.read_bytes()
                if hashlib.sha256(content).hexdigest()!=item['sha256']:raise ValueError('Local official PDF hash mismatch')
            else:content=publisher.request(item['url'],referer=item['page_url'])
            if not content.lstrip().startswith(b'%PDF-'):raise ValueError('Official link did not return a PDF')
            digest=hashlib.sha256(content).hexdigest()
            pdf=staging/(digest[:20]+'.pdf')
            atomic_write(pdf,content)
            cached={n:copy.deepcopy(d) for n,d in saved.items() if d.get('Factsheet Source',{}).get('SHA256')==digest}
            if cached and previous.get('extractor_version')==version:
                extracted=cached
            else:
                with PDF_LOCK:extracted=extract_factsheet(pdf)
            with PDF_LOCK:
                with pymupdf.open(stream=content,filetype='pdf') as doc:
                    pdf_text='\n'.join(p.get_text() for p in doc)
                    detected=detect_known_funds(pdf_text)
            for missing in set(detected)-set(extracted):failed_funds[missing]='The PDF contains the fund, but its layout has no verified adapter.'
            periods=[]
            for name,data in extracted.items():
                if FUND_HOUSES.get(name)!=house:raise ValueError('PDF identity does not belong to configured AMC')
                period=fund_period(data,pdf_text);periods.append(period)
                validation=data.get('Extraction Validation',{})
                if validation.get('Status') not in {'passed','passed_with_warnings'}:
                    failed_funds[name]='; '.join(validation.get('Errors',[]));continue
                data['Factsheet Source']={'Publishing Page':item['page_url'],'PDF URL':item['url'],
                    'Reporting Month':period,'SHA256':digest,'Local PDF':str(pdf.resolve()),
                    'Downloaded At':now,'Official Only':True,
                    'Latest Listing Verified':listing_verified and item['url'] in {i['url'] for i in discovered}}
                if not data.get('Portfolio Date') or not reporting_period(data.get('Portfolio Date','')):
                    data['Portfolio Date']=period
                old=funds.get(name)
                funds[name]=merge_editions(old,data)
            documents.append({'url':item['url'],'sha256':digest,'reporting_months':sorted(set(periods))})
        except Exception as e:errors.append(item['url']+': '+str(e))
    publisher.session.close()
    failed_funds={name:reason for name,reason in failed_funds.items() if name not in funds}
    # Never replace last good data with an unavailable or invalid edition.
    for name,data in saved.items():
        if FUND_HOUSES.get(name)!=house or 'Factsheet Source' not in data:continue
        if name not in funds or data['Factsheet Source']['Reporting Month']>funds[name]['Factsheet Source']['Reporting Month']:
            funds[name]=data
        elif data['Factsheet Source']['SHA256']==funds[name]['Factsheet Source']['SHA256']:
            # Re-extracting the same primary PDF must not drop previously
            # verified alternate plan editions absent from an offline run.
            editions=funds[name].setdefault('Additional Factsheet Editions',[])
            hashes={e['Factsheet Source']['SHA256'] for e in editions}
            for edition in data.get('Additional Factsheet Editions',[]):
                digest=edition['Factsheet Source']['SHA256']
                if digest not in hashes:
                    editions.append(edition);hashes.add(digest)
    paths=save_house_pdfs(directory,house,funds)
    scheme_files=save_scheme_files(directory,funds)
    for doc in documents:
        if doc['sha256'] in paths:doc['path']=paths[doc['sha256']]
    unchanged=previous.get('extractor_version')==version and {d['sha256'] for d in previous.get('documents',[])}=={d['sha256'] for d in documents}
    return {'fund_house':house,'checked_at':now,'listing_verified':listing_verified,
            'status':'error' if errors or failed_funds else (('unchanged' if unchanged else 'updated') if funds else 'unavailable'),
            'errors':errors,'failed_funds':failed_funds,'documents':documents,
            'extractor_version':version,
            'scheme_files':scheme_files,
            'fund_count':len(funds),'extraction_folder':str((directory/'latest/extraction').resolve())},funds

def coverage(profiles,state,all_funds):
    by_house={p['fund_house']:p for p in profiles};rows=[]
    for name,house in FUND_HOUSES.items():
        p=by_house[house];s=state['sources'].get(p['id'],{});data=all_funds.get(name)
        if data:
            source=data['Factsheet Source']
            latest=s.get('listing_verified',False) and source.get('Latest Listing Verified',False) and source['PDF URL'] in {d['url'] for d in s.get('documents',[])}
            row={'Scheme':name,'Fund House':house,'Status':'extracted',
                 'Reporting Month':source['Reporting Month'],'Portfolio Scope':data.get('Portfolio Scope','Published holdings'),
                 'Validation':data['Extraction Validation']['Status'],'Latest Listing Verified':latest,
                 'Warnings':data['Extraction Validation'].get('Warnings',[]),'PDF URL':source['PDF URL'],
                 'JSON File':'extraction/'+filename(name)+'.json'}
        else:
            reason=s.get('failed_funds',{}).get(name) or '; '.join(s.get('errors',[]))
            if not reason:reason='Scheme was absent from the latest retrievable official factsheet.'
            row={'Scheme':name,'Fund House':house,'Status':'extraction_failed' if name in s.get('failed_funds',{}) else 'unavailable',
                 'Reporting Month':None,'Reason':reason,'Publishing Pages':p['pages']}
        row['Checked At']=s.get('checked_at');rows.append(row)
    return {'Generated At':utc_now(),'Registered Schemes':len(rows),
            'Extracted Schemes':sum(r['Status']=='extracted' for r in rows),
            'Official Only':True,'Schemes':rows}

def publish_latest_pdfs(directory,funds,report):
    """Publish only primary PDFs selected by the latest per-scheme extractions.

    Remove only unchanged copies tracked by this publisher's previous manifest,
    never arbitrary files in the latest folder.
    """
    directory=Path(directory).resolve();latest=directory/'latest'
    latest.mkdir(parents=True,exist_ok=True)
    if latest.resolve().parent!=directory:raise ValueError('Latest output folder must remain inside the output directory')
    manifest=latest/'manifest.json'
    previous=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
    groups={};errors=[]
    freshness={r['Scheme']:r.get('Latest Listing Verified',False) for r in report.get('Schemes',[])}
    for name,data in funds.items():
        if data.get('Extraction Validation',{}).get('Status') not in {'passed','passed_with_warnings'}:continue
        source=data.get('Factsheet Source',{})
        if not source.get('Local PDF') or not source.get('SHA256'):
            errors.append(name+': No verified local PDF reference');continue
        key=(data['AMC / Fund House'],source['SHA256'])
        group=groups.setdefault(key,{'source':source,'schemes':[],'months':set()})
        group['schemes'].append(name);group['months'].add(source['Reporting Month'])
    counts={house:sum(h==house for h,_ in groups) for house,_ in groups}
    entries=[]
    for (house,digest),group in groups.items():
        source=group['source'];path=Path(source['Local PDF'])
        try:
            content=path.read_bytes()
            if hashlib.sha256(content).hexdigest()!=digest:raise ValueError('PDF checksum mismatch')
            stem=filename(house)
            if counts[house]>1:
                stem+=' - '+document_group(source['PDF URL'])+' - '+'_'.join(sorted(group['months']))+' - '+digest[:8]
            name=stem+'.pdf';atomic_write(latest/name,content)
            entries.append({'File Name':name,'Fund House':house,'Schemes':sorted(group['schemes']),
                'Reporting Months':sorted(group['months']),'PDF URL':source['PDF URL'],'SHA256':digest,
                'Latest Listing Verified':all(freshness.get(n,False) for n in group['schemes'])})
        except (OSError,ValueError) as exc:errors.append(house+': '+str(exc))
    selected={e['File Name'] for e in entries}
    for entry in previous.get('Files',[]):
        name=entry.get('File Name','')
        if not name or Path(name).name!=name or name in selected:continue
        path=latest/name
        if path.resolve().parent!=latest.resolve():continue
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==entry.get('SHA256'):
            path.unlink()
    scheme_files=save_scheme_files(directory,funds)
    extractions=[{'Scheme':name,'File Name':Path(info['path']).name,'SHA256':info['sha256']} for name,info in scheme_files.items()]
    current_names={e['File Name'] for e in extractions}
    folder=latest/'extraction'
    for entry in previous.get('Extractions',[]):
        name=entry.get('File Name','')
        if not name or Path(name).name!=name or name in current_names:continue
        path=folder/name
        if path.resolve().parent==folder.resolve() and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==entry.get('SHA256'):
            path.unlink()
    write_json(manifest,{'Generated At':utc_now(),'Selection':'Newest verified reporting month per scheme; primary PDF only',
        'PDF Count':len(entries),'Files':entries,'Extraction Count':len(extractions),'Extractions':extractions,'Errors':errors})
    LOG.info('Published %s current PDFs to %s',len(entries),latest)
    return entries

def run_once(profiles,directory,browser=True,offline=False):
    directory=Path(directory).resolve()
    with output_lock(directory):
        state_path=directory/'monitor_state.json'
        state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'sources':{}}
        all_funds={}
        def work(p):
            try:return p,refresh_house(p,directory,state['sources'].get(p['id'],{}),browser,offline)
            except Exception as e:
                previous=state['sources'].get(p['id'],{})
                saved=load_saved_schemes(p,directory,previous)
                scheme_files=save_scheme_files(directory,saved)
                result={**previous,'fund_house':p['fund_house'],'checked_at':utc_now(),'status':'error',
                        'listing_verified':False,'errors':[str(e)],'scheme_files':scheme_files}
                result.pop('json_sha256',None);result.pop('json_path',None)
                return p,(result,saved)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for p,(result,funds) in pool.map(work,profiles):
                state['sources'][p['id']]=result;all_funds.update(funds)
                LOG.info('%s: %s; %s schemes',p['fund_house'],result['status'],len(funds))
                state['last_run_at']=utc_now();write_json(state_path,state)
        report=coverage(profiles,state,all_funds);write_json(directory/'latest/coverage.json',report)
        publish_latest_pdfs(directory,all_funds,report)
        LOG.info('Extracted %s / %s registered schemes',report['Extracted Schemes'],report['Registered Schemes'])
        return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--once',action='store_true');p.add_argument('--watch',action='store_true')
    p.add_argument('--interval',type=int,default=3600)
    p.add_argument('--config',type=Path,default=ROOT/'official_sources.json')
    p.add_argument('--output-dir',type=Path,default=ROOT/'output')
    p.add_argument('--no-browser',action='store_true')
    p.add_argument('--offline',action='store_true',help='Re-extract previously downloaded official PDFs; does not verify latest listings.')
    args=p.parse_args()
    if args.interval<60:p.error('Interval must be at least 60 seconds')
    profiles=load_profiles(args.config)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
       handlers=[logging.StreamHandler(),logging.FileHandler(args.output_dir/'monitor.log',encoding='utf-8')])
    while True:
        report=run_once(profiles,args.output_dir,not args.no_browser,args.offline)
        if not args.watch:return 0 if report['Extracted Schemes'] else 1
        time.sleep(args.interval)

if __name__=='__main__':sys.exit(main())
