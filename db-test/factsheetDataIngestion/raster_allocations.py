"""Read printed labels in the quant comparative and asset-allocation images."""
import hashlib
import json
from pathlib import Path
import re
import tempfile

import pymupdf

from .market_cap import ocr_image


def chart_image(page,heading):
    hits=page.search_for(heading)
    if not hits:return None
    title=hits[0]
    images=[i for i in page.get_image_info(xrefs=True) if i['xref'] and i['bbox'][0]<180
            and 0<=i['bbox'][1]-title.y1<25]
    if len(images)!=1:raise ValueError('No unique raster image for '+heading)
    image=page.parent.extract_image(images[0]['xref'])
    return image,images[0]['bbox']


def _ocr_pixmap(pix,directory,name):
    path=Path(directory)/(name+'.png');pix.save(path)
    return ocr_image(path)


def relative_weights(page):
    """Read reviewed printed values only when the source image hash matches.

    Tiny rotated white labels (including zero and negative weights) do not
    round-trip reliably through Windows OCR. Reviewed transcriptions are
    bound to exact image bytes, never to a scheme name or reporting date.
    A changed image must be reviewed again; stale values cannot be reused.
    """
    found=chart_image(page,'RELATIVE WEIGHTS')
    if found is None:return []
    image,rectangle=found
    digest=hashlib.sha256(image['image']).hexdigest()
    reviewed=json.loads(Path(__file__).with_name('reviewed_raster_allocations.json').read_text(encoding='utf-8'))
    entry=reviewed.get(digest)
    if entry is None:
        raise ValueError('Unreviewed relative-weights image: '+digest)
    evidence={'Method':entry['Method'],'Image SHA256':digest,'Review Source':entry['Source']}
    return [{'Family':family,'Title':'Relative Weights','Basis':basis,'Source Page':page.number+1,
             'Source Rectangle':list(rectangle),'Scope':'Published rounded comparative weights; not rebased',
             'Rows':[{'Label':row['Label'],'Percentage':row[series],'Source Page':page.number+1} for row in entry['Rows']],
             'Evidence':evidence} for series,family,basis in [
                 ('Index','BENCHMARK_SECTOR','Index weightage'),('Scheme','SECTOR','Scheme weightage')]]


def asset_allocation(page):
    found=chart_image(page,'ASSET ALLOCATION')
    if found is None:return []
    image,rectangle=found
    with tempfile.TemporaryDirectory(prefix='sif-asset-ocr-') as directory:
        pix=pymupdf.Pixmap(image['image']);result=_ocr_pixmap(pix,directory,'asset')
    words=result['words'];rows=[]
    for word in words:
        if word['text'].lower() not in {'equity','debt','invit'}:continue
        cx=word['x']+word['width']/2
        candidates=[w for w in words if re.fullmatch(r'\d+(?:\.\d+)?',w['text'])
                    and 0<w['y']-word['y']<word['height']*3
                    and abs(w['x']+w['width']/2-cx)<word['width']]
        if len(candidates)!=1:raise ValueError('Ambiguous raster asset-allocation label.')
        rows.append({'Label':word['text'],'Percentage':float(candidates[0]['text']),'Source Page':page.number+1})
    if not rows:raise ValueError('No printed asset allocation labels recognized.')
    return [{'Family':'ASSET','Title':'Asset Allocation','Basis':'As published; not rebased to 100%',
             'Source Page':page.number+1,'Source Rectangle':list(rectangle),'Rows':rows,
             'Evidence':{'Method':'Raster chart labels (local OCR)','OCR Words':words,
                         'Image SHA256':hashlib.sha256(image['image']).hexdigest()}}]
