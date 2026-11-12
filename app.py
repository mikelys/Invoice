import csv
import hashlib
import io
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import fitz

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from ocr import extract, recognize

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('DATA_DIR', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)
(DATA / 'files').mkdir(exist_ok=True)
PASSWORD = os.environ.get('APP_PASSWORD', '')
security = HTTPBasic(auto_error=False)
CATEGORIES = ['办公用品', '软件服务', '交通出行', '餐饮招待', '差旅住宿', '日常采购', '其他费用']
CURRENCIES = ['CAD']


def authenticate(credentials: HTTPBasicCredentials | None = Depends(security)):
    if PASSWORD and (not credentials or not secrets.compare_digest(credentials.password.encode(), PASSWORD.encode())):
        raise HTTPException(401, '请使用应用密码登录（用户名可自定义）', headers={'WWW-Authenticate': 'Basic'})


app = FastAPI(title='Invoice · 发票管理', dependencies=[Depends(authenticate)])


@contextmanager
def db():
    conn = sqlite3.connect(DATA / 'invoices.sqlite', timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


with db() as conn:
    conn.execute('''CREATE TABLE IF NOT EXISTS invoices (
        id TEXT PRIMARY KEY, number TEXT NOT NULL DEFAULT '', vendor TEXT NOT NULL DEFAULT '',
        date TEXT NOT NULL DEFAULT '', amount_cents INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'CAD', category TEXT NOT NULL DEFAULT '其他费用',
        purpose TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
        file_name TEXT NOT NULL DEFAULT '', file_path TEXT NOT NULL DEFAULT '',
        file_hash TEXT UNIQUE, ocr_text TEXT NOT NULL DEFAULT '', ocr_error TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL)''')
    # This ledger contains CAD invoices only; correct previously inferred currency labels.
    conn.execute("UPDATE invoices SET currency='CAD' WHERE currency != 'CAD'")


@app.middleware('http')
async def guards(request: Request, call_next):
    if request.method in {'POST', 'PUT', 'DELETE'}:
        # Custom header cannot be sent by a cross-origin HTML form.
        if request.headers.get('x-invoice-client') != '1':
            return JSONResponse({'detail': '请求来源无效'}, status_code=403)
        length = request.headers.get('content-length')
        if length and int(length) > 21 * 1024 * 1024:
            return JSONResponse({'detail': '单个文件不能超过 20 MB'}, status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'self'; frame-src 'self'; base-uri 'self'; form-action 'self'"
    return response


def public(row):
    value = dict(row)
    value['amount'] = f"{value['amount_cents'] / 100:.2f}"
    value.pop('file_path', None)
    value.pop('file_hash', None)
    return value


def filtered(params):
    clauses, args = [], []
    for key in ['currency', 'status', 'category']:
        if params.get(key):
            clauses.append(f'{key} = ?')
            args.append(params[key])
    for key, op in [('start', '>='), ('end', '<=')]:
        if params.get(key):
            clauses.append(f'date {op} ?')
            args.append(params[key])
    if params.get('q'):
        clauses.append('(number LIKE ? OR vendor LIKE ? OR purpose LIKE ?)')
        args.extend([f"%{params['q']}%"] * 3)
    order = {'date_desc': 'date DESC, created_at DESC', 'date_asc': 'date ASC', 'amount_desc': 'amount_cents DESC', 'amount_asc': 'amount_cents ASC', 'newest': 'created_at DESC'}.get(params.get('sort'), 'date DESC, created_at DESC')
    with db() as conn:
        return conn.execute('SELECT * FROM invoices' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY ' + order, args).fetchall()


@app.get('/api/config')
def config():
    return {'categories': CATEGORIES, 'currencies': CURRENCIES, 'default_currency': 'CAD', 'password_enabled': bool(PASSWORD)}


@app.get('/api/invoices')
def invoices(request: Request):
    return [public(row) for row in filtered(request.query_params)]


@app.post('/api/upload')
async def upload(file: UploadFile):
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in {'.pdf', '.jpg', '.jpeg', '.png', '.webp'}:
        raise HTTPException(400, '支持 PDF、JPG、PNG 和 WebP 格式')
    content = await file.read(20 * 1024 * 1024 + 1)
    if not content or len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, '文件为空或超过 20 MB')
    digest = hashlib.sha256(content).hexdigest()
    with db() as conn:
        existing = conn.execute('SELECT id FROM invoices WHERE file_hash = ?', (digest,)).fetchone()
    if existing:
        raise HTTPException(409, '此文件已上传，请在发票列表中查看')
    identifier = uuid.uuid4().hex
    path = DATA / 'files' / (identifier + suffix)
    path.write_bytes(content)
    text, error = '', ''
    try:
        text = await run_in_threadpool(recognize, path)
    except Exception as exc:
        error = str(exc) if isinstance(exc, ValueError) else '文件无法识别，请检查格式，或手动填写字段'
    fields = extract(text)
    cents = int(Decimal(fields['amount'] or '0') * 100)
    # OCR output is untrusted, including numeric outliers.
    cents = cents if 0 <= cents <= 999_999_999_99 else 0
    try:
        with db() as conn:
            conn.execute('''INSERT INTO invoices
                (id,number,vendor,date,amount_cents,currency,category,purpose,file_name,file_path,file_hash,ocr_text,ocr_error,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (identifier, fields['number'], fields['vendor'], fields['date'], cents, fields['currency'], fields['category'], fields['purpose'], Path(file.filename).name, path.name, digest, text, error, datetime.now(timezone.utc).isoformat()))
            row = conn.execute('SELECT * FROM invoices WHERE id = ?', (identifier,)).fetchone()
    except sqlite3.IntegrityError:
        path.unlink(missing_ok=True)
        raise HTTPException(409, '此文件已上传')
    return public(row)


def validate(data):
    values = {}
    for key, limit in [('number', 100), ('vendor', 200), ('purpose', 1000), ('date', 10)]:
        values[key] = str(data.get(key, '')).strip()
        if len(values[key]) > limit:
            raise HTTPException(400, f'{key} 内容过长')
    values['status'] = data.get('status', 'pending')
    values['currency'] = data.get('currency', 'CAD')
    values['category'] = data.get('category', '其他费用')
    if values['status'] not in ['pending', 'confirmed'] or values['currency'] not in CURRENCIES or values['category'] not in CATEGORIES:
        raise HTTPException(400, '状态、币种或类别无效')
    try:
        amount = Decimal(str(data.get('amount') or '0'))
        if not amount.is_finite() or amount < 0 or amount > Decimal('999999999.99') or amount != amount.quantize(Decimal('0.01')):
            raise ValueError()
        values['amount_cents'] = int(amount * 100)
        if values['date']:
            date.fromisoformat(values['date'])
    except (ValueError, InvalidOperation):
        raise HTTPException(400, '请填写有效日期和金额（最多两位小数）')
    if values['status'] == 'confirmed' and (not values['date'] or not values['vendor'] or amount <= 0):
        raise HTTPException(400, '确认前请填写商户、日期和大于零的金额')
    return values


@app.post('/api/invoices')
def create(data: dict):
    values = validate(data)
    identifier = uuid.uuid4().hex
    with db() as conn:
        conn.execute('INSERT INTO invoices (id, created_at) VALUES (?,?)', (identifier, datetime.now(timezone.utc).isoformat()))
        conn.execute('UPDATE invoices SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), identifier])
        return public(conn.execute('SELECT * FROM invoices WHERE id=?', (identifier,)).fetchone())


@app.put('/api/invoices/{identifier}')
def update(identifier: str, data: dict):
    values = validate(data)
    with db() as conn:
        changed = conn.execute('UPDATE invoices SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?', [*values.values(), identifier])
        if not changed.rowcount:
            raise HTTPException(404, '未找到发票')
        return public(conn.execute('SELECT * FROM invoices WHERE id=?', (identifier,)).fetchone())


@app.delete('/api/invoices/{identifier}')
def delete(identifier: str):
    with db() as conn:
        row = conn.execute('SELECT file_path FROM invoices WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise HTTPException(404, '未找到发票')
        conn.execute('DELETE FROM invoices WHERE id=?', (identifier,))
    if row['file_path']:
        (DATA / 'files' / row['file_path']).unlink(missing_ok=True)
    return {'ok': True}


@app.get('/api/files/{identifier}')
def original(identifier: str):
    with db() as conn:
        row = conn.execute('SELECT file_path FROM invoices WHERE id=?', (identifier,)).fetchone()
    if not row or not row['file_path'] or not (DATA / 'files' / row['file_path']).is_file():
        raise HTTPException(404, '没有原始附件')
    return FileResponse(DATA / 'files' / row['file_path'], content_disposition_type='inline')


@app.get('/api/files/{identifier}/preview')
def preview(identifier: str, page: int = 1):
    with db() as conn:
        row = conn.execute('SELECT file_path FROM invoices WHERE id=?', (identifier,)).fetchone()
    if not row or not row['file_path']:
        raise HTTPException(404, '没有原始附件')
    path = DATA / 'files' / row['file_path']
    if not path.is_file():
        raise HTTPException(404, '原始附件不存在')
    if path.suffix != '.pdf':
        return FileResponse(path)
    try:
        with fitz.open(path) as doc:
            if not 1 <= page <= len(doc):
                raise HTTPException(400, '页码无效')
            content = doc[page-1].get_pixmap(dpi=120).tobytes('png')
            return Response(content, media_type='image/png', headers={'X-Page-Count': str(len(doc))})
    except (RuntimeError, ValueError):
        raise HTTPException(422, 'PDF 无法预览，请打开原件查看')


@app.post('/api/invoices/{identifier}/recognize')
async def rerecognize(identifier: str):
    with db() as conn:
        row = conn.execute('SELECT * FROM invoices WHERE id=?', (identifier,)).fetchone()
    if not row or not row['file_path']:
        raise HTTPException(404, '没有可识别的原始附件')
    if row['status'] == 'confirmed':
        raise HTTPException(400, '请先保存为待核对，再重新识别')
    try:
        text = await run_in_threadpool(recognize, DATA / 'files' / row['file_path'])
    except Exception as exc:
        raise HTTPException(422, str(exc) if isinstance(exc, ValueError) else '无法重新识别，请检查原件')
    fields = extract(text)
    cents = int(Decimal(fields.pop('amount') or '0') * 100)
    fields.update(amount_cents=cents if 0 <= cents <= 999_999_999_99 else 0, ocr_text=str(text), ocr_error='')
    # Preserve fields already reviewed or entered by the user. Fill only missing values.
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        current = conn.execute('SELECT * FROM invoices WHERE id=?', (identifier,)).fetchone()
        if not current or current['status'] != 'pending':
            raise HTTPException(409, '记录状态已改变，请重新打开')
        changes = {k: v for k, v in fields.items() if k in {'ocr_text', 'ocr_error'} or
                   (not current[k] or (k == 'category' and current[k] == '其他费用'))}
        changed = conn.execute('UPDATE invoices SET ' + ','.join(k+'=?' for k in changes) +
                              " WHERE id=? AND status='pending'", [*changes.values(), identifier])
        if not changed.rowcount:
            raise HTTPException(409, '记录状态已改变，请重新打开')
        return public(conn.execute('SELECT * FROM invoices WHERE id=?', (identifier,)).fetchone())


@app.get('/api/reports')
def reports(request: Request):
    params = dict(request.query_params)
    params['status'] = 'confirmed'
    totals, months, categories = {}, {}, {}
    for row in filtered(params):
        currency, cents = row['currency'], row['amount_cents']
        entry = totals.setdefault(currency, {'cents': 0, 'count': 0})
        entry['cents'] += cents
        entry['count'] += 1
        month = row['date'][:7]
        months.setdefault(currency, {})[month] = months.setdefault(currency, {}).get(month, 0) + cents
        categories.setdefault(currency, {})[row['category']] = categories.setdefault(currency, {}).get(row['category'], 0) + cents
    return {'totals': totals, 'months': months, 'categories': categories}


def safe_cell(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


@app.get('/api/export')
def export(request: Request):
    output = io.StringIO()
    writer = csv.writer(output)
    report = request.query_params.get('mode') == 'report'
    params = dict(request.query_params)
    if report:
        params['status'] = 'confirmed'
        writer.writerow(['月份', '费用类别', '币种', '金额', '发票数量'])
        groups = {}
        for row in filtered(params):
            key = (row['date'][:7], row['category'], row['currency'])
            cents, count = groups.get(key, (0, 0))
            groups[key] = (cents + row['amount_cents'], count + 1)
        for key, (cents, count) in sorted(groups.items()):
            writer.writerow([*key, f'{cents / 100:.2f}', count])
    else:
        writer.writerow(['系统ID', '发票号码', '商户', '日期', '金额', '币种', '类别', '用途', '状态'])
        for row in filtered(params):
            writer.writerow([safe_cell(row[k]) for k in ['id', 'number', 'vendor', 'date']] + [f"{row['amount_cents']/100:.2f}", row['currency'], row['category'], safe_cell(row['purpose']), '已确认' if row['status'] == 'confirmed' else '待核对'])
    return Response(('\ufeff' + output.getvalue()).encode(), media_type='text/csv; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="invoice-report.csv"'})


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
