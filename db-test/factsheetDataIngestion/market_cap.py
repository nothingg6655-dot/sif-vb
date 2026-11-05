"""Extract published market-cap splits from attributed tables and chart labels.

Never derive a split from top holdings, prose, bar heights, or the remaining
percentage. Totals need not equal 100: issuers publish different denominators.
"""
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import pymupdf

FIELD = 'Market Capitalization (%)'
BUCKETS = ('Large Cap', 'Mid Cap', 'Small Cap')
ROOT = Path(__file__).resolve().parent


def _complete(values):
    return set(values) == set(BUCKETS) and all(
        isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 100
        for value in values.values())


def ocr_image(path):
    """Return word positions using a local OCR engine; never upload source images."""
    tesseract = shutil.which('tesseract')
    if tesseract:
        result = subprocess.run([tesseract, str(path), 'stdout', '--psm', '11', 'tsv'],
            capture_output=True, text=True, encoding='utf-8-sig', check=True, timeout=60)
        import csv
        words = []
        for row in csv.DictReader(result.stdout.splitlines(), delimiter='\t'):
            if row.get('text', '').strip():
                words.append({'text': row['text'], 'x': float(row['left']), 'y': float(row['top']),
                    'width': float(row['width']), 'height': float(row['height'])})
        return {'words': words, 'engine': 'tesseract'}
    if os.name == 'nt':
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File',
            str(ROOT / 'windows_ocr.ps1'), '-ImagePath', str(Path(path).resolve())],
            capture_output=True, text=True, encoding='utf-8-sig', check=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return json.loads(result.stdout)
    raise RuntimeError('Raster market-cap chart requires Tesseract or Windows OCR.')


def bar_chart_values(words):
    """Pair each printed number with its x-aligned category, excluding axis ticks."""
    labels = {}
    for word in words:
        if word['text'].lower() in {'large', 'mid', 'small'}:
            labels[word['text'].title() + ' Cap'] = word
    if set(labels) != set(BUCKETS):
        raise ValueError('OCR did not identify all three market-cap category labels.')
    centers = sorted(word['x'] for word in labels.values())
    tolerance = min(b - a for a, b in zip(centers, centers[1:])) * .35
    result = {}
    for bucket, label in labels.items():
        # Category words start to the left of each bar's center. Include the
        # following "Cap" word when computing that center.
        cap = [word for word in words if word['text'].lower() == 'cap'
               and abs(word['y'] - label['y']) < label['height']
               and 0 < word['x'] - label['x'] < tolerance * 2]
        right = min(cap, key=lambda word: word['x']) if cap else None
        center = (label['x'] + (right['x'] + right['width'] if right else label['x'] + label['width'])) / 2
        candidates = [word for word in words if re.fullmatch(r'\d{1,3}(?:\.\d{1,3})?%?', word['text'])
                      and word['y'] + word['height'] < label['y']
                      and abs(word['x'] + word['width'] / 2 - center) < tolerance]
        if len(candidates) != 1:
            raise ValueError('Ambiguous/missing OCR value for ' + bucket)
        result[bucket] = float(candidates[0]['text'].rstrip('%'))
    if not _complete(result):
        raise ValueError('Invalid market-cap chart percentage.')
    return result


def raster_chart(page):
    headings = page.search_for('CONTRIBUTION BY MARKET CAP')
    if not headings:
        return None
    heading = headings[0]
    images = [info for info in page.get_image_info(xrefs=True) if info['xref']
              and abs(info['bbox'][0] - heading.x0) < 50
              and 0 <= info['bbox'][1] - heading.y1 < 30
              and 80 < info['bbox'][2] - info['bbox'][0] < 210]
    if len(images) != 1:
        raise ValueError('No unique raster chart below the market-cap heading.')
    info = images[0]
    content = page.parent.extract_image(info['xref'])
    with tempfile.TemporaryDirectory(prefix='sif-cap-ocr-') as directory:
        path = Path(directory) / ('chart.' + content['ext'])
        path.write_bytes(content['image'])
        ocr = ocr_image(path)
    return bar_chart_values(ocr['words']), {'method': 'raster_chart_ocr', 'engine': ocr['engine'],
        'page': page.number + 1, 'rectangle': list(info['bbox']),
        'image_sha256': hashlib.sha256(content['image']).hexdigest(), 'ocr_words': ocr['words']}


def _distance(first, second):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(first, second)))


def donut_chart(page):
    """Associate printed pie labels with category swatches by rendered color.

    Reading PDF text order alone swaps slices when their legend order changes.
    Sample the wedge on the label's ray, including labels outside the circle.
    """
    drawings = [d for d in page.get_drawings() if d.get('fill') and len(d['fill']) == 3]
    legend = {}
    label_rects = []
    for bucket, terms in [('Large Cap', ['Large Cap']), ('Mid Cap', ['Mid Cap', 'Midcap']),
                          ('Small Cap', ['Small Cap'])]:
        choices = []
        for term in terms:
            for label in page.search_for(term):
                for drawing in drawings:
                    box = drawing['rect']
                    if 2 < box.width < 12 and 2 < box.height < 12 and 0 < label.x0 - box.x1 < 12 \
                            and abs((box.y0 + box.y1 - label.y0 - label.y1) / 2) < 4:
                        choices.append((label, drawing))
        if len(choices) != 1:
            return None
        label, drawing = choices[0]
        label_rects.append(label)
        legend[bucket] = drawing['fill']
    top = min(r.y0 for r in label_rects)
    region = pymupdf.Rect(min(r.x0 for r in label_rects) - 45, top - 165,
                         max(r.x1 for r in label_rects) + 40, top - 3)
    wedges = [d for d in drawings if region.contains(d['rect'])
              and 6 < d['rect'].width < region.width and 6 < d['rect'].height < region.height
              and max(d['fill']) - min(d['fill']) > .15 and len(d['items']) >= 3]
    if len(wedges) < 3:
        return None
    bounds = pymupdf.Rect(wedges[0]['rect'])
    for wedge in wedges[1:]:
        bounds |= wedge['rect']
    center = ((bounds.x0 + bounds.x1) / 2, (bounds.y0 + bounds.y1) / 2)
    radius = min(bounds.width, bounds.height) / 2
    colors = [d['fill'] for d in wedges]
    pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
    result, evidence = {}, []
    for word in page.get_text('words'):
        if not re.fullmatch(r'\d{1,3}(?:\.\d{1,3})?%?', word[4]):
            continue
        x, y = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
        if not region.contains(pymupdf.Point(x, y)):
            continue
        distance = math.hypot(x - center[0], y - center[1])
        if not .35 * radius < distance < 1.9 * radius:
            continue
        angle = math.atan2(y - center[1], x - center[0])
        votes = []
        for factor in (.60, .70, .80, .90):
            px = int((center[0] + math.cos(angle) * radius * factor) * 3)
            py = int((center[1] + math.sin(angle) * radius * factor) * 3)
            sampled = tuple(channel / 255 for channel in pix.pixel(px, py)[:3])
            index = min(range(len(colors)), key=lambda i: _distance(colors[i], sampled))
            if _distance(colors[index], sampled) < .08:
                votes.append(index)
        if not votes:
            continue
        color = colors[Counter(votes).most_common(1)[0][0]]
        bucket = min(legend, key=lambda key: _distance(legend[key], color))
        if _distance(legend[bucket], color) > .16:
            continue
        if bucket in result:
            raise ValueError('Ambiguous donut label for ' + bucket)
        result[bucket] = float(word[4].rstrip('%'))
        evidence.append({'bucket': bucket, 'printed_label': word[4], 'rectangle': list(word[:4])})
    if not _complete(result):
        raise ValueError('Could not associate all three donut values with the legend.')
    return result, {'method': 'vector_donut_legend', 'page': page.number + 1,
                    'rectangle': list(bounds), 'labels': evidence}


def text_table(page, house):
    """Restrict parsing to the published section rather than adjacent prose/tables."""
    regions = []
    if house == 'ICICI Prudential Mutual Fund':
        for heading in page.search_for('Market Cap Break-Up Allocation Details'):
            regions.append(pymupdf.Rect(heading.x0 - 3, heading.y1, page.rect.width, heading.y1 + 130))
    elif house == 'Tata Mutual Fund':
        for heading in page.search_for('COMPOSITION BY RATINGS/MARKET CAP'):
            regions.append(pymupdf.Rect(25, heading.y1, page.rect.width / 2 + 5, heading.y1 + 70))
    elif house == 'Union Mutual Fund':
        for heading in page.search_for('Market Cap % of net assets (Equity)'):
            regions.append(pymupdf.Rect(400, heading.y1, page.rect.width, heading.y1 + 70))
    elif house == 'Invesco Mutual Fund':
        for heading in page.search_for('Market Capitalization'):
            regions.append(pymupdf.Rect(heading.x0, heading.y1, 198, heading.y1 + 50))
    elif house == 'ITI Mutual Fund':
        for heading in page.search_for('Market Capitalisation'):
            if heading.x0 > page.rect.width * .7:
                regions.append(pymupdf.Rect(heading.x0 - 3, heading.y1, page.rect.width, heading.y1 + 70))
    for region in regions:
        text = page.get_text(clip=region, sort=True)
        if house == 'ICICI Prudential Mutual Fund':
            result = {}
            for label in ('Large', 'Mid', 'Small'):
                match = re.search(label + r'\s+Cap Allocation\s+Gross\s*:\s*(\d+(?:\.\d+)?)%?\s*\|?\s*Net\s*:\s*(\d+(?:\.\d+)?)', text, re.I)
                if match:
                    result[label + ' Cap'] = {'Gross': float(match[1]), 'Net': float(match[2])}
            if len(result) == 3 and all(_complete({k: v[basis] for k, v in result.items()}) for basis in ('Gross', 'Net')):
                return result, {'method': 'gross_net_table', 'page': page.number + 1, 'rectangle': list(region), 'text': text}
        else:
            result = {}
            for label in ('Large', 'Mid', 'Small'):
                match = re.search(r'\b' + label + r'(?:\s*Cap)?\s*%?\s+(\d+(?:\.\d+)?)%?', text, re.I)
                if match:
                    result[label + ' Cap'] = float(match[1])
            if _complete(result):
                return result, {'method': 'published_table', 'page': page.number + 1, 'rectangle': list(region), 'text': text}
    return None


def enrich_market_cap(document, funds):
    """Enrich each scheme only from its attributed PDF pages; retain evidence."""
    for name, data in funds.items():
        house = data.get('AMC / Fund House')
        pages = [document[number - 1] for number in data.get('Source Pages', [])
                 if isinstance(number, int) and 0 < number <= len(document)]
        published = data.get(FIELD) or {}
        sources, errors = [], []
        for page in pages:
            try:
                found = text_table(page, house)
                if found is None and house == 'quant Mutual Fund':
                    found = raster_chart(page)
                if found is None and house in {'SBI Mutual Fund', 'Edelweiss Mutual Fund'}:
                    found = donut_chart(page)
                if found:
                    values, source = found
                    if published and published != values:
                        raise ValueError('Conflicting market-cap splits across attributed scheme pages.')
                    published = values
                    sources.append(source)
            except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
                errors.append(f'Page {page.number + 1}: {error}')
        # A strategy objective mentioning market capitalization is not a
        # published allocation section (nor are the SEBI rank definitions).
        headings = {
            'ICICI Prudential Mutual Fund': 'Market Cap Break-Up Allocation Details',
            'Tata Mutual Fund': 'COMPOSITION BY RATINGS/MARKET CAP',
            'Invesco Mutual Fund': 'Market Capitalization',
            'quant Mutual Fund': 'CONTRIBUTION BY MARKET CAP',
            'Edelweiss Mutual Fund': 'Market Cap (%)',
            'Union Mutual Fund': 'Market Cap % of net assets (Equity)',
        }
        heading_found = any(page.search_for(headings[house]) for page in pages) if house in headings else False
        if house == 'ITI Mutual Fund':
            heading_found = any(rect.x0 > page.rect.width * .7 for page in pages
                                for rect in page.search_for('Market Capitalisation'))
        if published and not errors:
            status = 'PUBLISHED'
            data[FIELD] = published
        elif errors or heading_found:
            status = 'PARSE_FAILED'
        else:
            status = 'NOT_PUBLISHED'
        data.setdefault('Field Availability', {})[FIELD] = status
        info = {'status': status, 'sources': sources, 'errors': errors,
                'inspected_pages': [page.number + 1 for page in pages],
                'units': 'Published percentages; denominator retained, not rescaled to 100%.'}
        if published and not sources:
            info['method'] = 'existing_verified_adapter'
        if status == 'NOT_PUBLISHED':
            info['note'] = 'No published market-cap split found on attributed scheme pages; no allocation inferred from holdings or strategy prose.'
        data.setdefault('Supplementary Fields', {})['Market Capitalization Evidence'] = info
        if status == 'PARSE_FAILED':
            validation = data.setdefault('Extraction Validation', {})
            warning = 'Market-cap section requires review: ' + ('; '.join(errors) or 'no verified numeric split extracted')
            validation.setdefault('Warnings', []).append(warning)
            if validation.get('Status') == 'passed':
                validation['Status'] = 'passed_with_warnings'
    return funds
