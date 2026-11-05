"""Migrate verified outputs to latest/PDFs and latest/extraction/scheme.json."""
import hashlib
import json
from pathlib import Path

from automation import output_lock,write_json,utc_now
from monitor import ROOT,allows,coverage,load_profiles,publish_latest_pdfs,save_house_pdfs,save_scheme_files
from fund_registry import FUND_HOUSES

def main():
    directory=ROOT/'output'
    if directory.resolve().parent!=ROOT.resolve():raise ValueError('Output folder outside workspace')
    with output_lock(directory):
        data=json.loads((directory/'all_sif_schemes.json').read_text(encoding='utf-8'))
        state=json.loads((directory/'monitor_state.json').read_text(encoding='utf-8'))
        profiles=load_profiles(ROOT/'official_sources.json')
        for profile in profiles:
            house=profile['fund_house']
            funds={n:d for n,d in data.items() if FUND_HOUSES.get(n)==house}
            if any(d['Extraction Validation']['Status'] not in {'passed','passed_with_warnings'} for d in funds.values()):
                raise ValueError('Unvalidated data in '+house)
            paths=save_house_pdfs(directory,house,funds)
            records=save_scheme_files(directory,funds)
            previous=state['sources'].setdefault(profile['id'],{})
            previous['scheme_files']=records
            previous['extraction_folder']=str((directory/'latest/extraction').resolve())
            previous.pop('json_sha256',None);previous.pop('json_path',None)
            sources={d['Factsheet Source']['SHA256']:d['Factsheet Source'] for d in funds.values()}
            previous['documents']=[{'url':s['PDF URL'],'sha256':digest,'path':paths[digest],
                                    'reporting_months':[s['Reporting Month']]} for digest,s in sources.items()]
            known=[]
            for digest,s in sources.items():
                if not allows(profile,s['PDF URL']):raise ValueError('Source URL not official')
                known.append({'url':s['PDF URL'],'page_url':s['Publishing Page'],
                              'local_path':str(Path(paths[digest]).relative_to(ROOT)).replace('\\','/'),
                              'sha256':digest})
            if known:profile['known_documents']=known
        report=coverage(profiles,state,data)
        entries=publish_latest_pdfs(directory,data,report)
        if sum(len(e['Schemes']) for e in entries)!=len(data):raise ValueError('Not all scheme PDFs were preserved')
        write_json(directory/'latest/coverage.json',report)
        state['storage_layout']='latest PDFs and scheme extraction files; no archive'
        state['reorganized_at']=utc_now()
        write_json(directory/'monitor_state.json',state)
        write_json(ROOT/'official_sources.json',{'official_only':True,'sources':profiles})
        for name,d in data.items():
            s=d['Factsheet Source'];pdf=Path(s['Local PDF'])
            if 'archive' in pdf.relative_to(directory).parts:raise ValueError('Unmigrated PDF reference')
            if hashlib.sha256(pdf.read_bytes()).hexdigest()!=s['SHA256']:raise ValueError('PDF hash changed')
        print('Preserved',len(entries),'latest PDFs and',len(data),'scheme JSON files. Archive removal is now safe.')

if __name__=='__main__':main()
