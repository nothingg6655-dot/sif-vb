"""Bundle verified per-scheme allocation/holdings JSON without changing source values.

Run after the factsheet monitor: python -m scripts.export_allocations_and_holdings
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
LATEST=ROOT/'data/factsheets/latest'
FIELDS=('Sector Allocation (%)','Asset Allocation (%)','Rating Profile (%)','Market Capitalization (%)')


def main():
    manifest=json.loads((LATEST/'manifest.json').read_text(encoding='utf-8'))
    coverage=json.loads((LATEST/'coverage.json').read_text(encoding='utf-8'))
    funds={};paths=[]
    for item in manifest['Extractions']:
        path=LATEST/'extraction'/item['File Name']
        if path.resolve().parent!=(LATEST/'extraction').resolve():raise ValueError('Invalid extraction path')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['SHA256']:raise ValueError('Extraction checksum mismatch: '+path.name)
        record=json.loads(path.read_text(encoding='utf-8'))
        if list(record)!=[item['Scheme']]:raise ValueError('Unexpected scheme identity: '+path.name)
        funds.update(record);paths.append(path)
    schemes=[]
    for name,data in funds.items():
        schemes.append({'Scheme':name,'Reporting Month':data['Factsheet Source']['Reporting Month'],
            'JSON File':'extraction/'+next(item['File Name'] for item in manifest['Extractions'] if item['Scheme']==name),
            'Allocations':{field:{'Status':data['Field Availability'].get(field,'UNKNOWN'),'Rows':len(data[field])} for field in FIELDS},
            'Holdings':len(data['Portfolio']),'Derivative Holdings':len(data['Derivative Portfolio']),
            'Portfolio Scope':data.get('Portfolio Scope'),
            'Published Allocation Groups':len(data.get('Supplementary Fields',{}).get('Published Allocation Groups',[]))})
    report={'Generated At':manifest['Generated At'],'Source PDFs':manifest['PDF Count'],'Registered Schemes':coverage['Registered Schemes'],
        'Extracted Schemes':len(funds),'Holdings':sum(row['Holdings'] for row in schemes),
        'Derivative Holdings':sum(row['Derivative Holdings'] for row in schemes),
        'Allocation Coverage':{field:dict(Counter(row['Allocations'][field]['Status'] for row in schemes)) for field in FIELDS},
        'Missing Reports':[row for row in coverage['Schemes'] if row['Status']!='extracted'],
        'Notes':['Percentages retain printed labels, precision, signs and denominators; no rebasing to 100%.',
            'Sector Allocation (%) is the primary published scheme split. Benchmark, cash/derivative and top-ten series have separate published allocation groups.',
            'Portfolio and Derivative Portfolio contain structured holdings; published aggregates are flagged Aggregate.',
            'Top-ten-only holdings remain labelled in Portfolio Scope; unpublished positions are not inferred.',
            'Five image-only relative-weights charts use visually reviewed printed values bound to exact image SHA256; changed images require a new review.',
            'Availability refers to the downloaded PDFs. This run did not verify online freshness.'],
        'Schemes':schemes}
    for path,value in [(LATEST/'extracted_factsheets.json',funds),(LATEST/'allocation-holdings-coverage.json',report)]:
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    archive=LATEST/'scheme-allocations-and-holdings.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in paths+[LATEST/'allocation-holdings-coverage.json',LATEST/'manifest.json']:
            bundle.write(path,path.relative_to(LATEST))
    print(json.dumps({key:report[key] for key in ['Source PDFs','Extracted Schemes','Holdings','Derivative Holdings','Allocation Coverage']},indent=2))
    print('Bundle: '+str(archive))

if __name__=='__main__':main()
