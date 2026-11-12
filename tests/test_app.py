"""Run with: python -m unittest discover -s tests -v"""
import base64
import io
import os
import tempfile
import unittest

import fitz
from fastapi.testclient import TestClient


class InvoiceFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ['DATA_DIR'] = cls.temp.name
        os.environ['APP_PASSWORD'] = 'test-password'
        import app
        cls.app = app
        cls.auth = 'Basic ' + base64.b64encode(b'owner:test-password').decode()
        cls.client = TestClient(app.app, headers={'X-Invoice-Client': '1', 'Authorization': cls.auth})

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.temp.cleanup()

    def test_end_to_end_pdf_and_cad_report(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), 'Invoice No: CA-2026-001\nVendor: Test Software\n2026-11-01\nGrand Total: CAD 123.45')
        pdf = doc.tobytes()
        doc.close()
        response = self.client.post('/api/upload', files={'file': ('invoice.pdf', pdf, 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        self.assertEqual((row['number'], row['amount'], row['currency'], row['date']), ('CA-2026-001', '123.45', 'CAD', '2026-11-01'))
        self.assertEqual(row['status'], 'pending')
        self.assertEqual(self.client.get('/api/reports').json()['totals'], {})
        self.assertEqual(self.client.post('/api/upload', files={'file': ('duplicate.pdf', pdf)}).status_code, 409)
        row['status'] = 'confirmed'
        self.assertEqual(self.client.put('/api/invoices/'+row['id'], json=row).status_code, 200)
        self.assertEqual(self.client.get('/api/reports?currency=CAD&start=2026-11-01&end=2026-11-30').json()['totals']['CAD']['cents'], 12345)
        usd = dict(row, currency='USD', amount='20.10', vendor='=HYPERLINK("unsafe")')
        self.assertEqual(self.client.post('/api/invoices', json=usd).status_code, 400)
        cadrow = self.client.post('/api/invoices', json=dict(usd, currency='CAD')).json()
        totals = self.client.get('/api/reports').json()['totals']
        self.assertEqual(set(totals), {'CAD'})
        self.assertEqual(totals['CAD']['cents'], 14355)
        self.assertEqual(self.client.get('/api/config').json()['currencies'], ['CAD'])
        exported = self.client.get('/api/export?currency=CAD').text
        self.assertIn("'=HYPERLINK", exported)
        report = self.client.get('/api/export?mode=report&currency=CAD').text
        self.assertIn('143.55,2', report)
        self.assertEqual(self.client.get('/api/files/'+row['id']).content, pdf)
        preview = self.client.get('/api/files/'+row['id']+'/preview')
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers['content-type'], 'image/png')
        self.assertTrue(preview.content.startswith(b'\x89PNG'))
        self.assertEqual(self.client.get('/api/files/'+row['id']+'/preview?page=0').status_code, 400)
        self.assertEqual(self.client.post('/api/invoices/'+row['id']+'/recognize').status_code, 400)
        self.assertEqual(self.client.get('/api/reports?end=2026-10-31').json()['totals'], {})
        self.assertEqual(self.client.delete('/api/invoices/'+row['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/files/'+row['id']).status_code, 404)
        self.client.delete('/api/invoices/'+cadrow['id'])

    def test_validation_and_authentication(self):
        anonymous = TestClient(self.app.app)
        self.assertEqual(anonymous.get('/api/invoices').status_code, 401)
        self.assertEqual(anonymous.get('/').status_code, 401)
        self.assertEqual(anonymous.get('/api/files/anything').status_code, 401)
        self.assertEqual(self.client.post('/api/invoices', json={'status':'confirmed','amount':'1'}).status_code, 400)
        for amount in ['NaN', 'Infinity', '-1', '1.001', '1000000000']:
            self.assertEqual(self.client.post('/api/invoices', json={'amount':amount}).status_code, 400)
        self.assertEqual(self.client.post('/api/invoices',json={'date':'2026-02-30'}).status_code,400)
        self.assertEqual(self.client.post('/api/upload',files={'file':('test.html',b'hello')}).status_code,400)
        without_header = TestClient(self.app.app, headers={'Authorization': self.auth})
        self.assertEqual(without_header.post('/api/invoices',json={}).status_code,403)

    def test_local_image_ocr(self):
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB', (1500, 600), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 40)
        draw.multiline_text((80,70),'Invoice No: CA-999\nVendor: Office Shop\n2026-11-02\nGrand Total: CAD 88.50',font=font,fill='black',spacing=30)
        out = io.BytesIO()
        image.save(out,format='PNG')
        response = self.client.post('/api/upload',files={'file':('scan.png',out.getvalue(),'image/png')})
        self.assertEqual(response.status_code,200,response.text)
        row=response.json()
        self.assertEqual(row['amount'],'88.50',row)
        self.assertEqual(row['number'],'CA-999',row)
        self.assertEqual(row['ocr_error'],'',row)
        self.assertEqual(row['vendor'], 'Office Shop', row)
        self.assertEqual(row['date'], '2026-11-02', row)
        self.assertEqual(self.client.get('/api/files/'+row['id']+'/preview').content, out.getvalue())
        self.client.delete('/api/invoices/'+row['id'])


    def test_header_vendor_pdf_layout_and_rerecognize(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 45), 'THANK YOU FOR SHOPPING', fontsize=9)
        page.insert_text((72, 80), 'MAPLE OFFICE SUPPLIES', fontsize=25, fontname='hebo')
        page.insert_text((72, 110), '123 Queen Street\nInvoice Date: 02-Nov-2026\nDue Date: 02-Dec-2026\nInvoice No: CAD-654\nTotal: $92.50', fontsize=11)
        response = self.client.post('/api/upload', files={'file': ('header.pdf', doc.tobytes(), 'application/pdf')})
        doc.close()
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        self.assertEqual(row['vendor'], 'MAPLE OFFICE SUPPLIES', row)
        self.assertEqual(row['date'], '2026-11-02', row)
        row.update(vendor='My reviewed merchant', date='', amount='0')
        self.assertEqual(self.client.put('/api/invoices/'+row['id'], json=row).status_code, 200)
        updated = self.client.post('/api/invoices/'+row['id']+'/recognize')
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['vendor'], 'My reviewed merchant')
        self.assertEqual(updated.json()['date'], '2026-11-02')
        self.assertEqual(updated.json()['amount'], '92.50')
        self.client.delete('/api/invoices/'+row['id'])

    def test_prominent_image_header(self):
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB', (1600, 900), 'white')
        draw = ImageDraw.Draw(image)
        normal = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 30)
        bold = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 65)
        draw.text((80, 20), 'THANK YOU FOR SHOPPING', font=normal, fill='black')
        draw.text((80, 100), 'MAPLE OFFICE SHOP', font=bold, fill='black')
        draw.multiline_text((80, 220), '123 Queen Street\nInvoice Date: Nov 2, 2026\nInvoice No: CA-888\nGrand Total: $88.50', font=normal, fill='black', spacing=25)
        out = io.BytesIO()
        image.save(out, format='PNG')
        response = self.client.post('/api/upload', files={'file': ('bold.png', out.getvalue(), 'image/png')})
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()
        self.assertEqual(row['vendor'], 'MAPLE OFFICE SHOP', row)
        self.assertEqual(row['date'], '2026-11-02', row)
        self.assertEqual(row['amount'], '88.50', row)
        self.client.delete('/api/invoices/'+row['id'])


class ExtractionTests(unittest.TestCase):
    def test_canadian_dates_and_priority(self):
        from ocr import extract
        for value in ['2026/11/02', 'Nov. 2, 2026', '2 November 2026', '02-Nov-26', '11/02/2026', '13/11/2026', '2026年11月2日']:
            expected = '2026-11-13' if value == '13/11/2026' else '2026-11-02'
            with self.subTest(value=value):
                self.assertEqual(extract('MAPLE SHOP\nInvoice Date: '+value)['date'], expected)
        self.assertEqual(extract('Due Date: Dec 2, 2026\nInvoice Date: Nov 2, 2026')['date'], '2026-11-02')
        self.assertEqual(extract('Invoice Date: 2026-02-30')['date'], '')
        self.assertEqual(extract('Invoice Date:\nNov 2, 2026\nDue Date: Dec 2, 2026')['date'], '2026-11-02')

    def test_header_vendor_and_fixed_currency(self):
        from ocr import extract
        self.assertEqual(extract('INVOICE\nMAPLE HARDWARE\n123 Main Street\nTotal: USD 12.00')['vendor'], 'MAPLE HARDWARE')
        self.assertEqual(extract('Vendor: Explicit Shop\nMAPLE HARDWARE')['vendor'], 'Explicit Shop')
        self.assertEqual(extract('THANK YOU FOR SHOPPING\nINVOICE\nNov 2, 2026\nMAPLE HARDWARE')['vendor'], 'MAPLE HARDWARE')
        for label in ['USD', 'EUR', 'CNY', 'CAD']:
            self.assertEqual(extract('Total: '+label+' 12.00')['currency'], 'CAD')


if __name__ == '__main__':
    unittest.main()
