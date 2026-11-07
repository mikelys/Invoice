"""Offline extraction. All results require human review before reporting."""
import re
import subprocess
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import fitz
from PIL import Image, ImageOps


def recognize(path: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        images = []
        parts = []
        if path.suffix == '.pdf':
            with fitz.open(path) as doc:
                if len(doc) > 20:
                    raise ValueError('单份 PDF 最多支持 20 页，请拆分上传')
                for i, page in enumerate(doc):
                    text = page.get_text()
                    if len(text.strip()) > 30:
                        parts.append(text)
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
                    result = subprocess.run(['tesseract', str(img), 'stdout', '-l', language], capture_output=True, text=True, timeout=90)
                    if result.returncode:
                        raise ValueError('扫描件识别失败，请使用清晰图片重试或手动录入')
                    parts.append(result.stdout)
            except FileNotFoundError:
                raise ValueError('未安装 Tesseract OCR，请按 README 安装，或手动录入')
        return '\n'.join(parts)[:100_000]


def extract(text: str) -> dict:
    result = {'number': '', 'vendor': '', 'date': '', 'amount': '', 'currency': 'CAD', 'category': '其他费用', 'purpose': ''}
    match = re.search(r'(?:发票号码|发票号|Invoice\s*(?:No\.?|Number|#))\s*[:：#]?\s*([A-Za-z0-9-]{3,40})', text, re.I)
    if match:
        result['number'] = match[1]
    match = re.search(r'(20\d{2})[年/.-]\s*(\d{1,2})[月/.-]\s*(\d{1,2})', text)
    if match:
        try:
            result['date'] = date(*map(int, match.groups())).isoformat()
        except ValueError:
            pass
    if not result['date']:
        match = re.search(r'\b([A-Za-z]{3,9}\s+\d{1,2},?\s+20\d{2})\b', text)
        if match:
            for fmt in ['%B %d %Y', '%b %d %Y']:
                try:
                    result['date'] = datetime.strptime(match[1].replace(',', ''), fmt).date().isoformat()
                    break
                except ValueError:
                    pass
    for pattern in [r'(?:价税合计[^\n\d]{0,20}|合计|总计|grand\s*total|amount\s*due|total)\s*[:：]?\s*(?:CNY|RMB|USD|EUR|GBP|HKD|CAD|CA\$|C\$|[¥￥$€£])?\s*([\d,]+\.\d{2})', r'[¥￥$€£]\s*([\d,]+\.\d{2})']:
        matches = re.findall(pattern, text, re.I)
        if matches:
            try:
                result['amount'] = str(max(Decimal(v.replace(',', '')) for v in matches))
            except InvalidOperation:
                pass
            break
    for currency, pattern in [('CNY', r'\bCNY\b|\bRMB\b|[¥￥]'), ('USD', r'\bUSD\b|US\$'), ('EUR', r'\bEUR\b|€'), ('GBP', r'\bGBP\b|£'), ('HKD', r'\bHKD\b|HK\$'), ('CAD', r'\bCAD\b|CA\$|C\$')]:
        if re.search(pattern, text, re.I):
            result['currency'] = currency
    match = re.search(r'(?:销售方名称|销售方|Seller|Vendor|Supplier)\s*[:：]\s*([^\n]{2,80})', text, re.I)
    if match:
        result['vendor'] = match[1].strip()
    match = re.search(r'(?:用途|项目名称|货物名称|服务名称|Description|Purpose)\s*[:：]\s*([^\n]{2,200})', text, re.I)
    if match:
        result['purpose'] = match[1].strip()
    categories = {'餐饮招待': r'餐饮|餐费|restaurant|coffee|dining', '交通出行': r'交通|车费|铁路|taxi|uber|flight|train', '办公用品': r'办公|文具|office|stationery', '软件服务': r'软件|订阅|software|subscription|cloud', '差旅住宿': r'住宿|酒店|hotel|lodging', '日常采购': r'采购|超市|supermarket|grocery'}
    for category, pattern in categories.items():
        if re.search(pattern, text, re.I):
            result['category'] = category
            break
    return result
