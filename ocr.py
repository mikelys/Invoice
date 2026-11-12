"""Offline extraction. All results require human review before reporting."""
import csv
import io
import re
import statistics
import subprocess
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import fitz
from PIL import Image, ImageOps


class RecognizedText(str):
    """Keep layout hints alongside text without changing the stored OCR format."""
    def __new__(cls, text, lines):
        obj = super().__new__(cls, text)
        obj.lines = lines
        return obj


def recognize(path: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        images = []
        parts = []
        layout = []
        if path.suffix == '.pdf':
            with fitz.open(path) as doc:
                if len(doc) > 20:
                    raise ValueError('单份 PDF 最多支持 20 页，请拆分上传')
                for i, page in enumerate(doc):
                    text = page.get_text(sort=True)
                    if len(text.strip()) > 30:
                        parts.append(text)
                        for block in page.get_text('dict', sort=True)['blocks']:
                            for line in block.get('lines', []):
                                spans = line['spans']
                                if spans:
                                    layout.append({'text': ''.join(s['text'] for s in spans),
                                                   'height': max(s['size'] for s in spans),
                                                   'bold': any(s['flags'] & 16 for s in spans)})
                    else:
                        target = Path(tmp) / f'page-{i}.png'
                        page.get_pixmap(dpi=180).save(target)
                        images.append(target)
        else:
            with Image.open(path) as img:
                if img.width * img.height > 40_000_000:
                    raise ValueError('图片尺寸过大，请压缩后上传')
                target = Path(tmp) / 'scan.png'
                ImageOps.exif_transpose(img).convert('RGB').save(target)
                images.append(target)
        if images:
            try:
                langs = subprocess.run(['tesseract', '--list-langs'], capture_output=True, text=True, timeout=10).stdout
                language = 'chi_sim+eng' if 'chi_sim' in langs else 'eng'
                for img in images:
                    result = subprocess.run(['tesseract', str(img), 'stdout', '-l', language, 'tsv'], capture_output=True, text=True, timeout=90)
                    if result.returncode:
                        raise ValueError('扫描件识别失败，请使用清晰图片重试或手动录入')
                    lines = {}
                    for word in csv.DictReader(io.StringIO(result.stdout), delimiter='\t', quoting=csv.QUOTE_NONE):
                        if word.get('level') != '5' or not word.get('text', '').strip():
                            continue
                        key = tuple(word[k] for k in ('page_num', 'block_num', 'par_num', 'line_num'))
                        line = lines.setdefault(key, {'words': [], 'height': 0, 'bold': False})
                        line['words'].append(word['text'])
                        line['height'] = max(line['height'], int(word['height']))
                    for line in lines.values():
                        line['text'] = ' '.join(line.pop('words'))
                        layout.append(line)
                        parts.append(line['text'])
            except FileNotFoundError:
                raise ValueError('未安装 Tesseract OCR，请按 README 安装，或手动录入')
        return RecognizedText('\n'.join(parts)[:100_000], layout)


MONTHS = {name.lower(): i for i, names in enumerate([
    ('Jan', 'January'), ('Feb', 'February'), ('Mar', 'March'), ('Apr', 'April'),
    ('May', 'May'), ('Jun', 'June'), ('Jul', 'July'), ('Aug', 'August'),
    ('Sep', 'September', 'Sept'), ('Oct', 'October'), ('Nov', 'November'), ('Dec', 'December')
], 1) for name in names}
MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December|Sept|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?'


def extract_date(text):
    candidates = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    patterns = [
        (r'(?<!\d)(20\d{2})\s*[年/.-]\s*(\d{1,2})\s*[月/.-]\s*(\d{1,2})(?!\d)', 'ymd'),
        (r'(?<!\d)(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(20\d{2}|\d{2})(?!\d)', 'numeric'),
        (rf'\b({MONTH})\s*[- /]?\s*(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s*[- /]?\s*(20\d{{2}}|\d{{2}})\b', 'mdy'),
        (rf'\b(\d{{1,2}})(?:st|nd|rd|th)?\s*[- /]?\s*({MONTH})[,]?\s*[- /]?\s*(20\d{{2}}|\d{{2}})\b', 'dmy'),
    ]
    for i, line in enumerate(lines):
        # Date labels sometimes occupy a separate line from the value.
        context = line + (' ' + lines[i-1] if i else '')
        score = 40 if i < 12 else 0
        if re.search(r'invoice\s*date|receipt\s*date|transaction\s*date|purchase\s*date|开票日期|发票日期', context, re.I):
            score += 100
        elif re.search(r'\bdate\b|日期', line, re.I):
            score += 30
        if re.search(r'due\s*date|payment\s*due|到期|截止', line, re.I):
            score -= 200
        for pattern, kind in patterns:
            for match in re.finditer(pattern, line, re.I):
                a, b, c = match.groups()
                try:
                    if kind == 'ymd':
                        year, month, day = int(a), int(b), int(c)
                    else:
                        year = int(c) + (2000 if len(c) == 2 else 0)
                        if kind == 'mdy':
                            month, day = MONTHS[a.rstrip('.').lower()], int(b)
                        elif kind == 'dmy':
                            month, day = MONTHS[b.rstrip('.').lower()], int(a)
                        else:
                            # Ambiguous Canadian numeric dates use MM/DD; values >12 disambiguate DD/MM.
                            month, day = (int(b), int(a)) if int(a) > 12 else (int(a), int(b))
                    value = date(year, month, day).isoformat()
                except (ValueError, KeyError):
                    continue
                candidates.append((score, -i, -match.start(), value))
    return max(candidates)[-1] if candidates else ''


def extract_vendor(text):
    labeled = re.search(r'(?:销售方名称|销售方|Seller|Vendor|Supplier|Merchant)\s*[:：]\s*([^\n]{2,80})', text, re.I)
    if labeled:
        return labeled[1].strip()
    lines = getattr(text, 'lines', None) or [
        {'text': line.strip(), 'height': 0, 'bold': False} for line in text.splitlines() if line.strip()]
    heights = [line['height'] for line in lines if line['height'] > 0]
    typical = statistics.median(heights) if heights else 1
    candidates = []
    for i, line in enumerate(lines[:12]):
        value = line['text'].strip(' |\t')
        if not 2 <= len(value) <= 100 or not re.search(r'[^\W\d_]', value, re.UNICODE):
            continue
        if re.search(r'^(?:tax\s+)?(?:invoice|receipt|statement)(?:\b|\s|#)|发票|收据|\b(?:date|total|subtotal|tax|hst|gst|pst|tel|phone|fax|www|http|customer|bill\s*to|ship\s*to|sold\s*to|order|cashier|register|transaction|description|qty|quantity)\b|@|\d{3}[- .)]*\d{3}[- .]*\d{4}|^\d+\s+\w+', value, re.I):
            continue
        if extract_date(value) or re.search(r'^thank\s*you\b|^welcome\b', value, re.I):
            continue
        if sum(c.isdigit() for c in value) > len(value) * .35:
            continue
        score = 40 - i * 3 + min(line['height'] / typical, 3) * 12
        score += 20 if line.get('bold') else 0
        score += 5 if value.isupper() else 0
        candidates.append((score, -i, value))
    return max(candidates)[-1] if candidates else ''


def extract(text: str) -> dict:
    result = {'number': '', 'vendor': '', 'date': '', 'amount': '', 'currency': 'CAD', 'category': '其他费用', 'purpose': ''}
    match = re.search(r'(?:发票号码|发票号|Invoice\s*(?:No\.?|Number|#))\s*[:：#]?\s*([A-Za-z0-9-]{3,40})', text, re.I)
    if match:
        result['number'] = match[1]
    result['date'] = extract_date(text)
    for pattern in [r'(?:价税合计[^\n\d]{0,20}|合计|总计|grand\s*total|amount\s*due|total)\s*[:：]?\s*(?:CNY|RMB|USD|EUR|GBP|HKD|CAD|CA\$|C\$|[¥￥$€£])?\s*([\d,]+\.\d{2})', r'[¥￥$€£]\s*([\d,]+\.\d{2})']:
        matches = re.findall(pattern, text, re.I)
        if matches:
            try:
                result['amount'] = str(max(Decimal(v.replace(',', '')) for v in matches))
            except InvalidOperation:
                pass
            break
    result['vendor'] = extract_vendor(text)
    match = re.search(r'(?:用途|项目名称|货物名称|服务名称|Description|Purpose)\s*[:：]\s*([^\n]{2,200})', text, re.I)
    if match:
        result['purpose'] = match[1].strip()
    categories = {'餐饮招待': r'餐饮|餐费|restaurant|coffee|dining', '交通出行': r'交通|车费|铁路|taxi|uber|flight|train', '办公用品': r'办公|文具|office|stationery', '软件服务': r'软件|订阅|software|subscription|cloud', '差旅住宿': r'住宿|酒店|hotel|lodging', '日常采购': r'采购|超市|supermarket|grocery'}
    for category, pattern in categories.items():
        if re.search(pattern, text, re.I):
            result['category'] = category
            break
    return result
