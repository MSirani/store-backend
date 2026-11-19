import os
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from app.db import get_connection, init_db
from app.main import app


class SellerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "shop.db"
        os.environ["STORE_DB_PATH"] = str(self.path)
        self.cm = TestClient(app)
        self.client = self.cm.__enter__()

    def tearDown(self):
        self.cm.__exit__(None, None, None)
        self.temp.cleanup()
        os.environ.pop("STORE_DB_PATH", None)

    def account(self, email, account_type="seller"):
        r = self.client.post('/auth/register/' + account_type, json={'name':'Test', 'email': email,
                            'password': 'long-secure-password-0123'})
        self.assertEqual(201,r.status_code,r.text)
        r = self.client.post('/auth/login/' + account_type, json={'email':email,
                            'password': 'long-secure-password-0123'})
        self.assertEqual(200,r.status_code,r.text)
        return {'Authorization':'Bearer '+r.json()['access_token']}

    def product(self, headers):
        r = self.client.post('/shops/me/products', json={
            'name':'Mouse', 'description':'Demo', 'price_toman':30000,'stock':5
        }, headers=headers)
        self.assertEqual(201,r.status_code,r.text)
        return r.json()

    def test_seller_flow_and_isolation(self):
        seller = self.account('seller@example.com')
        buyer = self.account('buyer@example.com', 'customer')
        other = self.account('other@example.com')
        self.assertEqual(403,self.client.post('/shops/me/products',json={
            'name':'Hacker','price_toman':1,'stock':1},headers=buyer).status_code)
        r = self.client.post('/shops',json={'name':'Tech Market'},headers=seller)
        self.assertEqual(201,r.status_code,r.text)
        shop_id = r.json()['id']
        self.assertEqual('seller',self.client.get('/auth/me',headers=seller).json()['account_type'])
        self.assertEqual('customer',self.client.get('/auth/me',headers=buyer).json()['account_type'])
        self.assertEqual(409,self.client.post('/shops',json={'name':'Second Shop'},headers=seller).status_code)
        self.assertEqual(shop_id,self.client.get('/shops/me',headers=seller).json()['id'])
        self.assertEqual(403,self.client.get('/shops/me',headers=buyer).status_code)
        p = self.product(seller)
        self.assertEqual(shop_id,p['shop_id'])
        self.assertEqual(1,len(self.client.get('/shops/me/products',headers=seller).json()))
        self.assertEqual(1,len(self.client.get('/products').json()))
        r = self.client.post('/shops',json={'name':'Another Shop'},headers=other)
        self.assertEqual(201,r.status_code,r.text)
        outsider = self.product(other)
        self.assertEqual(404,self.client.patch(f"/shops/me/products/{p['id']}",
            json={'price_toman':1},headers=other).status_code)
        self.assertEqual(409,self.client.post(f"/shops/me/products/{p['id']}/stock",
            json={'delta':10},headers=other).status_code)
        self.assertEqual(404,self.client.delete(f"/shops/me/products/{p['id']}",
            headers=other).status_code)
        self.assertEqual(200,self.client.patch(f"/shops/me/products/{p['id']}",
            json={'price_toman':35000},headers=seller).status_code)
        self.assertEqual(8,self.client.post(f"/shops/me/products/{p['id']}/stock",
            json={'delta':3},headers=seller).json()['quantity_available'])
        self.assertEqual(409,self.client.post(f"/shops/me/products/{p['id']}/stock",
            json={'delta':-99},headers=seller).status_code)
        self.assertEqual(35000,self.client.get(f"/products/{p['id']}").json()['price_toman'])
        self.assertEqual(204,self.client.delete(f"/shops/me/products/{p['id']}",
            headers=seller).status_code)
        self.assertEqual(0,self.client.get(f"/products/{p['id']}").json()['active'])
        self.assertEqual(1,len(self.client.get('/products').json()))
        self.assertEqual(1,len(self.client.get('/shops/me/products',headers=seller).json()))
        self.assertEqual(outsider['id'],self.client.get('/products').json()[0]['id'])

    def test_shop_orders_are_scoped(self):
        seller = self.account('seller@example.com')
        other = self.account('other@example.com')
        buyer = self.account('buyer@example.com', 'customer')
        for head, name in ((seller, 'S1'),(other,'S2')):
            self.assertEqual(201,self.client.post('/shops',json={'name':name},headers=head).status_code)
        p1 = self.product(seller)
        p2 = self.product(other)
        addr = self.client.post('/addresses',json={
            'label':'home','recipient_name':'Test','phone':'12345678','city':'Tehran',
            'street':'street','postal_code':'1234567890'
        }, headers=buyer).json()['id']
        for p in (p1,p2):
            self.assertEqual(201,self.client.post('/cart/items',json={
                'product_id':p['id'],'quantity':1},headers=buyer).status_code)
        r = self.client.post('/orders',json={'address_id':addr,'method':'wallet'},headers=buyer)
        self.assertEqual(201,r.status_code,r.text)
        s1 = self.client.get('/shops/me/orders',headers=seller).json()
        s2 = self.client.get('/shops/me/orders',headers=other).json()
        self.assertEqual(1,len(s1))
        self.assertEqual(1,len(s2))
        self.assertEqual(p1['id'],s1[0]['product_id'])
        self.assertEqual(p2['id'],s2[0]['product_id'])
        self.assertNotIn('buyer_email',s1[0])
        self.assertEqual(403,self.client.get('/shops/me/orders',headers=buyer).status_code)

    def test_existing_product_schema_migration_keeps_rows(self):
        self.cm.__exit__(None,None,None)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('INSERT INTO products(name, price_toman, active) VALUES (?,?,1)', ('old',1000))
        init_db()
        with get_connection() as conn:
            row=conn.execute("SELECT name, shop_id FROM products WHERE name='old'").fetchone()
            self.assertEqual('old',row['name'])
            self.assertIsNone(row['shop_id'])
        self.cm = TestClient(app)
        self.client = self.cm.__enter__()


if __name__ == '__main__':
    unittest.main()
