"""Role isolation, order privacy and independent per-seller shipping tests."""
import os
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app.db import get_connection, init_db
from app.main import app

PASS = "long-safe-demo-password-2026"

class RoleAndShippingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "store.db"
        os.environ["STORE_DB_PATH"] = str(self.db)
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        os.environ.pop("STORE_DB_PATH", None)
        self.temp.cleanup()

    def account(self, email, kind):
        register = self.client.post(f"/auth/register/{kind}", json={
            "name": email.split('@')[0], "email": email, "password": PASS,
        })
        self.assertEqual(register.status_code, 201, register.text)
        login = self.client.post(f"/auth/login/{kind}", json={
            "email": email, "password": PASS,
        })
        self.assertEqual(login.status_code, 200, login.text)
        return {"Authorization": "Bearer " + login.json()["access_token"]}

    def test_login_role_mismatch_registration_and_shop_permission(self):
        seller = self.account('seller@example.com', 'seller')
        buyer = self.account('buyer@example.com', 'customer')
        wrong = self.client.post('/auth/login/customer', json={
            'email': 'seller@example.com', 'password': PASS,
        })
        self.assertEqual(wrong.status_code, 401)
        wrong = self.client.post('/auth/login/seller', json={
            'email': 'buyer@example.com', 'password': PASS,
        })
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(self.client.get('/auth/me',headers=seller).json()['role'], 'seller')
        self.assertEqual(self.client.get('/auth/me',headers=buyer).json()['role'], 'customer')
        denied = self.client.post('/shops',json={'name':'Not Allowed'},headers=buyer)
        self.assertEqual(denied.status_code, 403)
        allowed = self.client.post('/shops',json={'name':'Seller Shop'},headers=seller)
        self.assertEqual(allowed.status_code, 201)
        no_admin = self.client.post('/auth/register/seller',json={
            'name':'Hacker','email':'evil@example.com','password':PASS,'role':'admin'
        })
        self.assertEqual(no_admin.status_code, 422)
        self.assertEqual('seller',self.client.get('/auth/me',headers=seller).json()['account_type'])
        with get_connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0])

    def test_mixed_shop_order_info_and_shipping_are_separate(self):
        s1 = self.account('seller1@example.com', 'seller')
        s2 = self.account('seller2@example.com', 'seller')
        buyer = self.account('buyer@example.com', 'customer')
        for header, name in ((s1,'One Shop'), (s2,'Two Shop')):
            self.assertEqual(201,self.client.post('/shops', json={'name':name}, headers=header).status_code)
        p1 = self.client.post('/shops/me/products', json={
            'name':'Mouse','stock':5,'price_toman':10000
        }, headers=s1).json()['id']
        p2 = self.client.post('/shops/me/products', json={
            'name':'Keyboard','stock':5,'price_toman':12000
        }, headers=s2).json()['id']
        addr = self.client.post('/addresses', json={
            'label':'home','recipient_name':'Shipping Name','phone':'09123456789',
            'city':'Tehran','street':'Private Street','postal_code':'1234567890',
        }, headers=buyer).json()['id']
        for p in (p1,p2):
            self.assertEqual(201, self.client.post('/cart/items',json={
                'product_id':p,'quantity':1
            },headers=buyer).status_code)
        # Pending payment: no personal shipment information before payment.
        order = self.client.post('/orders', json={
            'address_id':addr,'method':'gateway'
        },headers=buyer)
        self.assertEqual(201,order.status_code,order.text)
        oid = order.json()['id']
        for seller, owned, other in ((s1,p1,p2),(s2,p2,p1)):
            data = self.client.get('/shops/me/orders',headers=seller).json()
            self.assertEqual(1,len(data))
            item=data[0]
            self.assertEqual(owned,item['product_id'])
            self.assertNotEqual(other,item['product_id'])
            self.assertIsNone(item['recipient_phone'])
            self.assertIsNone(item['buyer_name'])
            self.assertEqual('awaiting_payment',item['shipping_status'])
            self.assertNotIn('buyer_email',item)
            denied = self.client.patch(f'/shops/me/orders/{oid}/shipping',
                json={'status':'shipped'},headers=seller)
            self.assertEqual(409,denied.status_code)
        self.client.post(f'/orders/{oid}/cancel',headers=buyer)
        for seller in (s1,s2):
            self.assertEqual('cancelled',self.client.get('/shops/me/orders',headers=seller).json()[0]['shipping_status'])
        # Paid order with both seller lines (wallet is enough).
        for p in (p1,p2):
            self.assertEqual(201,self.client.post('/cart/items',json={
                'product_id':p,'quantity':1
            },headers=buyer).status_code)
        order = self.client.post('/orders',json={
            'address_id':addr,'method':'wallet'
        },headers=buyer)
        self.assertEqual(201,order.status_code,order.text)
        oid = order.json()['id']
        first = self.client.get('/shops/me/orders',headers=s1).json()[0]
        second = self.client.get('/shops/me/orders',headers=s2).json()[0]
        self.assertEqual((first['product_id'],second['product_id']),(p1,p2))
        self.assertEqual(first['buyer_name'],'buyer')
        self.assertEqual(first['recipient_name'],'Shipping Name')
        self.assertEqual(first['recipient_phone'],'09123456789')
        self.assertEqual(first['shipping_street'],'Private Street')
        self.assertEqual(first['shipping_status'],'processing')
        self.assertEqual(second['shipping_status'],'processing')
        self.assertEqual(403,self.client.patch(f'/shops/me/orders/{oid}/shipping',
            json={'status':'shipped'},headers=buyer).status_code)
        self.assertEqual(200,self.client.patch(f'/shops/me/orders/{oid}/shipping',
            json={'status':'shipped'},headers=s1).status_code)
        self.assertEqual('shipped',self.client.get('/shops/me/orders',headers=s1).json()[0]['shipping_status'])
        self.assertEqual('processing',self.client.get('/shops/me/orders',headers=s2).json()[0]['shipping_status'])
        self.assertEqual(409,self.client.patch(f'/shops/me/orders/{oid}/shipping',
            json={'status':'shipped'},headers=s1).status_code)
        self.assertEqual(409,self.client.patch(f'/shops/me/orders/{oid}/shipping',
            json={'status':'delivered'},headers=s2).status_code)
        self.assertEqual(200,self.client.patch(f'/shops/me/orders/{oid}/shipping',
            json={'status':'delivered'},headers=s1).status_code)
        self.assertEqual(404,self.client.patch('/shops/me/orders/999/shipping',
            json={'status':'shipped'},headers=s1).status_code)

    def test_existing_seller_and_order_are_migrated_without_id_loss(self):
        # Stop app to modify DB into previous schema state.
        self.client_cm.__exit__(None,None,None)
        # Build a minimal prior-version DB instead of relying on external files.
        self.db.unlink()
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript('''
            CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE,
              password_hash TEXT, role TEXT NOT NULL DEFAULT 'customer' CHECK(role IN ('customer','admin')));
            CREATE TABLE shops(id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE, name TEXT);
            CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '', price_toman INTEGER NOT NULL,
              active INTEGER NOT NULL DEFAULT 1, shop_id INTEGER);
            CREATE TABLE addresses(id INTEGER PRIMARY KEY, user_id INTEGER, label TEXT,
              recipient_name TEXT, phone TEXT, city TEXT, street TEXT, postal_code TEXT);
            CREATE TABLE orders(id INTEGER PRIMARY KEY, user_id INTEGER, address_id INTEGER,
              status TEXT, total_toman INTEGER, wallet_amount_toman INTEGER, gateway_amount_toman INTEGER,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER,
              quantity INTEGER, unit_price_toman INTEGER);
            ''')
            conn.execute("INSERT INTO users(id, name, email, password_hash) VALUES(7,'Old Seller','old@example.com','x')")
            conn.execute("INSERT INTO users(id, name, email, password_hash) VALUES(8,'Old Buyer','buyer@example.com','x')")
            conn.execute("INSERT INTO shops(id,user_id,name) VALUES(9,7,'Legacy Shop')")
            conn.execute("INSERT INTO products(id,name,price_toman,shop_id) VALUES(10,'Legacy Mouse',1000,9)")
            conn.execute("INSERT INTO addresses(id,user_id,label,recipient_name,phone,city,street,postal_code) VALUES (11,8,'home','B','12345','City','St','1234')")
            conn.execute("INSERT INTO orders(id,user_id,address_id,status,total_toman,wallet_amount_toman,gateway_amount_toman) VALUES (12,8,11,'paid',1000,1000,0)")
            conn.execute("INSERT INTO order_items(id,order_id,product_id,quantity,unit_price_toman) VALUES (13,12,10,1,1000)")
        init_db()
        init_db()  # idempotent migration
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT account_type FROM users WHERE id=7").fetchone()[0],'seller')
            self.assertEqual(conn.execute("SELECT account_type FROM users WHERE id=8").fetchone()[0],'customer')
            self.assertEqual(conn.execute("SELECT order_id,shop_id FROM shop_fulfillments").fetchall()[0][:], (12,9))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM order_items WHERE id=13").fetchone()[0],1)
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()

if __name__ == '__main__':
    unittest.main()
