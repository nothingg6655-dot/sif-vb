"""Read published SIF risk-band labels from attributed pages and chart images.

Scale ticks are never interpreted as the selected level. Raster charts use local
OCR of explicit captions, or a visually reviewed transcription bound to exact
image bytes when the selected level is indicated only by a circle and arrow.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata

import pymupdf

try:
    from .date_utils import parse_flexible_date
    from .market_cap import ocr_image
except ImportError:  # Support the existing standalone app.py entry point.
    from date_utils import parse_flexible_date
    from market_cap import ocr_image

FIELD = 'Risk Band & Strategy'
STRATEGY = 'Investment Strategy Risk Band'
BENCHMARK = 'Benchmark Risk Band'
LEVEL = re.compile(r'\bRISK[\s-]*(?:BAND[\s-]*)?LEVEL[\s:-]*([1-5])\b', re.I)
_OCR_CACHE = {}


def caption_candidates(words, page_number, method):
    text = ''
    spans = []
    for word in words:
        value = unicodedata.normalize('NFKC', str(word['text'])).replace('\u2013', '-').replace('\u2011', '-')
        start = len(text)
        text += value + ' '
        spans.append((start, len(text), word['rect']))
    candidates = []
    for match in LEVEL.finditer(text):
        rectangles = [rect for start, end, rect in spans if start < match.end() and end > match.start()]
        rect = pymupdf.Rect(rectangles[0])
        for other in rectangles[1:]:
            rect |= pymupdf.Rect(other)
        candidates.append({'level': int(match[1]), 'rectangle': list(rect), 'page': page_number,
                           'method': method, 'caption': match[0]})
    return candidates


def paired_levels(candidates):
    """Scheme chart is left of benchmark; vertically stacked charts read top first."""
    rows = []
    for candidate in sorted(candidates, key=lambda c: (pymupdf.Rect(c['rectangle']).y0,
                                                       pymupdf.Rect(c['rectangle']).x0)):
        rect = pymupdf.Rect(candidate['rectangle'])
        cy = (rect.y0 + rect.y1) / 2
        group = next((r for r in rows if abs(r[0] - cy) <= 10), None)
        if group is None:
            rows.append([cy, [candidate]])
        else:
            group[1].append(candidate)
    pairs = []
    for _, group in rows:
        group.sort(key=lambda c: pymupdf.Rect(c['rectangle']).x0)
        if len(group) == 2:
            a, b = [pymupdf.Rect(c['rectangle']) for c in group]
            if b.x0 - a.x0 > 20:
                pairs.append((group[0], group[1]))
    # Only chart-image candidates may be paired vertically. This prevents the
    # previous/current-month strategy risk table from becoming a benchmark band.
    if not pairs and len(candidates) == 2 and all(c['method'].startswith('raster') for c in candidates):
        ordered = sorted(candidates, key=lambda c: pymupdf.Rect(c['rectangle']).y0)
        a, b = [pymupdf.Rect(c['rectangle']) for c in ordered]
        if abs(a.x0 - b.x0) < 15 and b.y0 - a.y0 > 15:
            pairs.append(tuple(ordered))
    return pairs


def risk_images(page, house):
    images = [info for info in page.get_image_info(xrefs=True) if info['xref']]
    result = []
    headings = [rect for rect in page.search_for('RISK BAND') if rect.width > 25]
    for info in images:
        rect = pymupdf.Rect(info['bbox'])
        if house == 'quant Mutual Fund':
            selected = rect.x0 < 180 and 40 < rect.width < 120 and rect.height < 35 and any(
                0 <= rect.y0 - title.y1 < 55 for title in headings)
        elif house == 'Edelweiss Mutual Fund':
            selected = page.number == 0 and rect.y0 > page.rect.height * .7 and rect.width > page.rect.width * .8
        elif house == 'Tata Mutual Fund':
            selected = rect.x0 > page.rect.width / 2 and rect.y0 > page.rect.height * .8 and 75 < rect.width < 150
        elif house == 'The Wealth Company Mutual Fund':
            selected = rect.x0 > page.rect.width * .3 and rect.y0 > page.rect.height * .6 and rect.width > 180
        elif house == 'HSBC Mutual Fund':
            titles = page.search_for('Investment Strategy Risk Band') + page.search_for('Benchmark Risk Band')
            selected = rect.width > 70 and rect.height < 50 and any(
                abs(rect.x0 - title.x0) < 50 and 0 <= rect.y0 - title.y1 < 65 for title in titles)
        else:
            selected = False
        if selected:
            result.append(info)
    return result


def raster_candidates(page, info):
    image = page.parent.extract_image(info['xref'])
    digest = hashlib.sha256(image['image']).hexdigest()
    rect = pymupdf.Rect(info['bbox'])
    reviewed = json.loads(Path(__file__).with_name('reviewed_risk_bands.json').read_text(encoding='utf-8'))
    if digest in reviewed:
        item = reviewed[digest]
        return [{'level': item['Level'], 'rectangle': list(rect), 'page': page.number + 1,
                 'method': 'raster-reviewed-selected-indicator', 'image_sha256': digest,
                 'caption': item['Method']}]
    # Quant embeds a high-resolution chart. Other publishers overlay captions
    # as PDF drawing paths or transparency masks; render that complete region.
    if page.get_text().find('qsif') < 0:
        pix = page.get_pixmap(matrix=pymupdf.Matrix(4, 4), clip=rect, alpha=False)
        image = {'image': pix.tobytes('png'), 'ext': 'png', 'width': pix.width, 'height': pix.height}
    render_digest = hashlib.sha256(image['image']).hexdigest()
    if render_digest not in _OCR_CACHE:
        with tempfile.TemporaryDirectory(prefix='sif-risk-ocr-') as directory:
            path = Path(directory) / ('risk.' + image['ext'])
            path.write_bytes(image['image'])
            _OCR_CACHE[render_digest] = ocr_image(path)
    result = _OCR_CACHE[render_digest]
    words = []
    for word in result['words']:
        x, y = word['x'], word['y']
        box = [rect.x0 + x / image['width'] * rect.width,
               rect.y0 + y / image['height'] * rect.height,
               rect.x0 + (x + word['width']) / image['width'] * rect.width,
               rect.y0 + (y + word['height']) / image['height'] * rect.height]
        words.append({'text': word['text'], 'rect': box})
    candidates = caption_candidates(words, page.number + 1, 'raster-caption-ocr')
    unique = {c['level'] for c in candidates}
    # A small image is one chart with duplicate captions. Wide images contain
    # both scheme and benchmark charts, even when their levels are identical.
    if len(unique) == 1 and rect.width < 180:
        candidates = [{**candidates[0], 'rectangle': list(rect)}]
    for candidate in candidates:
        candidate['image_sha256'] = digest
        candidate['ocr_image_sha256'] = render_digest
        candidate['ocr_engine'] = result.get('engine')
        candidate['ocr_words'] = result['words']
    return candidates


def risk_as_of(pages):
    dates = set()
    pattern = re.compile(r'risk[ -]?band[^.\n]{0,100}?as (?:on|of)\s+([^\n.]+)', re.I)
    for page in pages:
        for match in pattern.finditer(page.get_text()):
            value = parse_flexible_date(match[1].strip())
            if value:
                dates.add(value.isoformat())
    return next(iter(dates)) if len(dates) == 1 else None


def enrich_risk_bands(document, funds):
    for name, data in funds.items():
        pages = [document[n - 1] for n in data.get('Source Pages', [])
                 if isinstance(n, int) and 0 < n <= len(document)]
        pairs, errors = [], []
        for page in pages:
            words = [{'text': w[4], 'rect': w[:4]} for w in page.get_text('words')]
            found = paired_levels(caption_candidates(words, page.number + 1, 'pdf-explicit-caption'))
            if not found:
                candidates = []
                for info in risk_images(page, data['AMC / Fund House']):
                    try:
                        candidates.extend(raster_candidates(page, info))
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                        errors.append(f'Page {page.number + 1}: {error}')
                found = paired_levels(candidates)
            pairs.extend(found)
        values = {(a['level'], b['level']) for a, b in pairs}
        risk = data.setdefault(FIELD, {})
        if len(values) == 1:
            strategy, benchmark = next(iter(values))
            incoming = {STRATEGY: f'Level {strategy}', BENCHMARK: f'Level {benchmark}'}
            if any(risk.get(key) and risk[key] != value for key, value in incoming.items()):
                errors.append('Published risk captions conflict with the existing verified band values.')
            else:
                risk.update(incoming)
                risk['Risk Band As Of'] = risk_as_of(pages)
                risk.setdefault('Benchmark', data.get('Fund Details', {}).get('Benchmark'))
        elif len(values) > 1:
            errors.append('Conflicting risk-band pairs on attributed scheme pages.')
        complete = bool(risk.get(STRATEGY) and risk.get(BENCHMARK))
        status = 'PUBLISHED' if complete and not errors else 'PARSE_FAILED'
        data.setdefault('Field Availability', {})[FIELD] = status
        for field in (STRATEGY, BENCHMARK):
            data['Field Availability'][f'{FIELD}/{field}'] = 'PUBLISHED' if risk.get(field) else 'PARSE_FAILED'
        data.setdefault('Supplementary Fields', {})['Risk Band Evidence'] = {
            'Status': status, 'Inspected Pages': [page.number + 1 for page in pages],
            'Source Pairs': [{'Investment Strategy': a, 'Benchmark': b} for a, b in pairs],
            'Errors': errors,
            'Note': 'Only explicit selected-level captions or checksum-bound reviewed indicators are accepted.',
        }
        if status == 'PARSE_FAILED':
            validation = data.setdefault('Extraction Validation', {})
            if validation.get('Status') == 'passed':
                validation['Status'] = 'passed_with_warnings'
            warning = 'Risk-band extraction requires review: ' + ('; '.join(errors) or 'no unambiguous published pair extracted')
            if warning not in validation.setdefault('Warnings', []):
                validation['Warnings'].append(warning)
    return funds
