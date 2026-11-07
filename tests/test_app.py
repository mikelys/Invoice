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

    def test_end_to_end_pdf_and_currency_report(self):
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
        usdrow = self.client.post('/api/invoices', json=usd).json()
        totals = self.client.get('/api/reports').json()['totals']
        self.assertEqual(totals['USD']['cents'], 2010)
        self.assertEqual(totals['CAD']['cents'], 12345)
        exported = self.client.get('/api/export?currency=USD').text
        self.assertIn("'=HYPERLINK", exported)
        report = self.client.get('/api/export?mode=report&currency=CAD').text
        self.assertIn('123.45,1', report)
        self.assertEqual(self.client.get('/api/files/'+row['id']).content, pdf)
        self.assertEqual(self.client.get('/api/reports?end=2026-10-31').json()['totals'], {})
        self.assertEqual(self.client.delete('/api/invoices/'+row['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/files/'+row['id']).status_code, 404)
        self.client.delete('/api/invoices/'+usdrow['id'])

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
        self.client.delete('/api/invoices/'+row['id'])


if __name__ == '__main__':
    unittest.main()
