"""Package current PDFs and scheme-named extractions, with no historical files."""
import hashlib
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent/'output'
LATEST=ROOT/'latest'

def main():
    manifest=json.loads((LATEST/'manifest.json').read_text(encoding='utf-8'))
    target=ROOT/'Latest Factsheets.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as bundle:
        for entry in manifest['Files']:
            name=entry['File Name']
            if Path(name).name!=name:raise ValueError('Invalid manifest filename')
            path=LATEST/name
            if path.resolve().parent!=LATEST.resolve():raise ValueError('PDF outside latest folder')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['SHA256']:
                raise ValueError('PDF checksum changed: '+name)
            bundle.write(path,name)
        bundle.write(LATEST/'manifest.json','manifest.json')
        for entry in manifest.get('Extractions',[]):
            name=entry['File Name']
            if Path(name).name!=name:raise ValueError('Invalid extraction filename')
            path=LATEST/'extraction'/name
            if path.resolve().parent!=(LATEST/'extraction').resolve():raise ValueError('Extraction outside output folder')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['SHA256']:raise ValueError('Extraction checksum changed')
            bundle.write(path,'extraction/'+name)
        if (LATEST/'coverage.json').exists():bundle.write(LATEST/'coverage.json','coverage.json')
    with zipfile.ZipFile(target) as bundle:
        if bundle.testzip():raise ValueError('ZIP integrity check failed')
    print('Packaged',manifest['PDF Count'],'latest PDFs and',manifest.get('Extraction Count',0),'scheme extractions:',target.resolve())

if __name__=='__main__':main()
