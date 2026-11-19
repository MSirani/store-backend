"""Run: python -m unittest discover -s tests -v"""
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import get_connection, init_db
from app.main import app


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "store.db"
        os.environ["STORE_DB_PATH"] = str(self.path)
        os.environ["ENABLE_DEV_PAYMENT_SETTLEMENT"] = "1"
        self.client_cm = TestClient(app)
        self.client = self.client_cm.__enter__()
        self.user, self.auth = self.register_and_login("alice@example.com")
        self.admin, self.admin_auth = self.register_and_login("admin@example.com")
        with get_connection(write=True) as conn:
            conn.execute("UPDATE users SET role='admin' WHERE id=?", (self.admin["id"],))
        self.address = self.client.post("/addresses", json={
            "label": "خانه", "recipient_name": "Ali", "phone": "09123456789",
            "city": "Tehran", "street": "Test street 10", "postal_code": "1234567890"
        }, headers=self.auth).json()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.temp.cleanup()
        os.environ.pop("STORE_DB_PATH", None)
        os.environ.pop("ENABLE_DEV_PAYMENT_SETTLEMENT", None)

    def register_and_login(self, email):
        registered = self.client.post("/auth/register", json={
            "name": "Test Person", "email": email, "password": "a very long passphrase!"
        })
        self.assertEqual(201, registered.status_code, registered.text)
        logged = self.client.post("/auth/login", json={
            "email": email, "password": "a very long passphrase!"
        })
        self.assertEqual(200, logged.status_code, logged.text)
        return registered.json(), {"Authorization": "Bearer " + logged.json()["access_token"]}

    def new_product(self, stock=5, price=30000):
        r = self.client.post("/products", json={
            "name": "Laptop demo", "description": "Test", "price_toman": price, "stock": stock
        }, headers=self.admin_auth)
        self.assertEqual(201, r.status_code, r.text)
        return r.json()

    def add_to_cart(self, product_id, qty=1, headers=None):
        return self.client.post("/cart/items", json={
            "product_id": product_id, "quantity": qty
        }, headers=headers or self.auth)

    def checkout(self, method="wallet", wallet_amount=0, headers=None, address=None):
        return self.client.post("/orders", json={
            "address_id": address or self.address["id"],
            "method": method,
            "wallet_amount_toman": wallet_amount,
        }, headers=headers or self.auth)

    def test_register_bonus_once_and_token_hash(self):
        w = self.client.get("/wallet", headers=self.auth).json()
        self.assertEqual(100000, w["balance_toman"])
        again = self.client.post("/auth/register", json={
            "name": "Test Person", "email": "alice@example.com", "password": "a very long passphrase!"
        })
        self.assertEqual(409, again.status_code)
        history = self.client.get("/wallet/transactions", headers=self.auth).json()
        self.assertEqual(1, len(history))
        self.assertEqual("signup_bonus", history[0]["kind"])
        with get_connection() as conn:
            token_hash = conn.execute("SELECT token_hash FROM sessions LIMIT 1").fetchone()[0]
        self.assertEqual(64, len(token_hash))

    def test_auth_required_admin_protected(self):
        self.assertEqual(401, self.client.get("/cart").status_code)
        self.assertEqual(403, self.client.post("/products", json={
            "name": "No", "price_toman": 1, "stock": 1
        }, headers=self.auth).status_code)
        r = self.client.get("/auth/me", headers=self.auth)
        self.assertEqual(200, r.status_code)

    def test_products_categories_and_old_cart_behavior(self):
        p = self.new_product()
        c = self.client.post("/categories", json={"name": "Laptop"}, headers=self.admin_auth)
        self.assertEqual(201, c.status_code)
        self.client.post(f"/products/{p['id']}/categories/{c.json()['id']}", headers=self.admin_auth)
        self.assertEqual(1, len(self.client.get(f"/products?category_id={c.json()['id']}").json()))
        self.assertEqual(201, self.add_to_cart(p["id"], 2).status_code)
        self.assertEqual(201, self.add_to_cart(p["id"], 1).status_code)
        self.assertEqual(3, self.client.get("/cart", headers=self.auth).json()["items"][0]["quantity"])
        # Adding to cart doesn't reserve any stock.
        self.assertEqual(5, self.client.get(f"/products/{p['id']}").json()["quantity_available"])

    def test_full_wallet_purchase_and_snapshot(self):
        p = self.new_product(stock=2, price=30000)
        self.add_to_cart(p["id"], 2)
        r = self.checkout()
        self.assertEqual(201, r.status_code, r.text)
        order = r.json()
        self.assertEqual("paid", order["status"])
        self.assertEqual(30000, order["items"][0]["unit_price_toman"])
        self.assertEqual(40000, self.client.get("/wallet", headers=self.auth).json()["balance_toman"])
        self.assertEqual(0, self.client.get(f"/products/{p['id']}").json()["quantity_available"])
        self.assertEqual(0, self.client.get(f"/products/{p['id']}").json()["quantity_reserved"])
        self.client.patch(f"/products/{p['id']}", json={"price_toman": 50000}, headers=self.admin_auth)
        after = self.client.get(f"/orders/{order['id']}", headers=self.auth).json()
        self.assertEqual(30000, after["items"][0]["unit_price_toman"])

    def test_gateway_cancel_releases_stock(self):
        p = self.new_product(stock=1)
        self.add_to_cart(p["id"])
        order = self.checkout("gateway").json()
        self.assertEqual("pending_payment", order["status"])
        self.assertEqual(0, self.client.get(f"/products/{p['id']}").json()["quantity_available"])
        self.assertEqual(1, self.client.get(f"/products/{p['id']}").json()["quantity_reserved"])
        cancel = self.client.post(f"/orders/{order['id']}/cancel", headers=self.auth)
        self.assertEqual(200, cancel.status_code)
        self.assertEqual(1, self.client.get(f"/products/{p['id']}").json()["quantity_available"])
        self.assertEqual(0, self.client.get(f"/products/{p['id']}").json()["quantity_reserved"])
        self.assertEqual(409, self.client.post(f"/orders/{order['id']}/cancel", headers=self.auth).status_code)

    def test_mixed_success_debits_once_and_failure_releases(self):
        p = self.new_product(stock=2, price=90000)
        self.add_to_cart(p["id"])
        r = self.checkout("mixed", wallet_amount=50000)
        self.assertEqual(201, r.status_code, r.text)
        order = r.json()
        w = self.client.get("/wallet", headers=self.auth).json()
        self.assertEqual((100000, 50000, 50000),
                         (w["balance_toman"], w["reserved_toman"], w["spendable_toman"]))
        gateway_id = next(p["id"] for p in order["payments"] if p["method"] == "gateway")
        settled = self.client.post(f"/dev/payments/{gateway_id}/settle", json={"success": True},
                                   headers=self.admin_auth)
        self.assertEqual(200, settled.status_code, settled.text)
        self.assertEqual(409, self.client.post(f"/dev/payments/{gateway_id}/settle", json={"success": True},
                                              headers=self.admin_auth).status_code)
        w = self.client.get("/wallet", headers=self.auth).json()
        self.assertEqual((50000, 0), (w["balance_toman"], w["reserved_toman"]))
        self.assertEqual("paid", self.client.get(f"/orders/{order['id']}", headers=self.auth).json()["status"])
        self.assertEqual(0, self.client.get(f"/products/{p['id']}").json()["quantity_reserved"])
        # A new mixed order fails payment, returning wallet hold and stock.
        self.add_to_cart(p["id"])
        second = self.checkout("mixed", wallet_amount=10000).json()
        gateway_id = next(p["id"] for p in second["payments"] if p["method"] == "gateway")
        failed = self.client.post(f"/dev/payments/{gateway_id}/settle", json={"success": False},
                                  headers=self.admin_auth)
        self.assertEqual(200, failed.status_code, failed.text)
        w = self.client.get("/wallet", headers=self.auth).json()
        self.assertEqual((50000, 0), (w["balance_toman"], w["reserved_toman"]))
        self.assertEqual("cancelled", self.client.get(f"/orders/{second['id']}", headers=self.auth).json()["status"])

    def test_admin_credit_is_idempotent_and_wallet_cannot_overdraw(self):
        first = self.client.post(f"/admin/wallets/{self.user['id']}/credit", json={
            "amount_toman": 25000, "event_key": "manual-test-credit-00001"
        }, headers=self.admin_auth)
        self.assertEqual(200, first.status_code, first.text)
        repeated = self.client.post(f"/admin/wallets/{self.user['id']}/credit", json={
            "amount_toman": 25000, "event_key": "manual-test-credit-00001"
        }, headers=self.admin_auth)
        self.assertEqual(409, repeated.status_code)
        self.assertEqual(125000, self.client.get("/wallet", headers=self.auth).json()["balance_toman"])
        expensive = self.new_product(stock=2, price=130000)
        self.add_to_cart(expensive["id"])
        failed = self.checkout()
        self.assertEqual(409, failed.status_code, failed.text)
        self.assertEqual(2, self.client.get(f"/products/{expensive['id']}").json()["quantity_available"])

    def test_no_oversell_and_atomic_multi_product_rollback(self):
        p1 = self.new_product(stock=2, price=10000)
        p2 = self.new_product(stock=0, price=10000)
        self.add_to_cart(p1["id"], 1)
        self.add_to_cart(p2["id"], 1)
        r = self.checkout()
        self.assertEqual(409, r.status_code, r.text)
        self.assertEqual(2, self.client.get(f"/products/{p1['id']}").json()["quantity_available"])
        self.assertEqual([], self.client.get("/orders", headers=self.auth).json())

    def test_concurrent_last_unit_only_one_checkout(self):
        p = self.new_product(stock=1, price=10000)
        self.add_to_cart(p["id"], 1)
        other, other_auth = self.register_and_login("other@example.com")
        other_addr = self.client.post("/addresses", json={
            "label": "home", "recipient_name": "Other", "phone": "09123456789",
            "city": "Tehran", "street": "Street", "postal_code": "1234567890"
        }, headers=other_auth).json()
        self.add_to_cart(p["id"], 1, headers=other_auth)
        def attempt(args):
            headers, address_id = args
            return self.checkout(headers=headers, address=address_id).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(attempt, [(self.auth, self.address["id"]),
                                            (other_auth, other_addr["id"])]))
        self.assertEqual([201, 409], sorted(codes))
        self.assertEqual(0, self.client.get(f"/products/{p['id']}").json()["quantity_available"])


class LegacyMigrationTests(unittest.TestCase):
    def test_existing_products_and_stock_are_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'legacy.db'
            os.environ['STORE_DB_PATH'] = str(path)
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price_toman INTEGER NOT NULL, stock INTEGER NOT NULL DEFAULT 0)')
            conn.execute("INSERT INTO products(name, price_toman, stock) VALUES ('Legacy', 999, 7)")
            conn.commit()
            conn.close()
            init_db()
            init_db()  # repeat is safe and must not duplicate inventory
            with get_connection() as conn:
                row = conn.execute('SELECT p.name, i.quantity_available FROM products p JOIN inventory i ON i.product_id=p.id').fetchone()
                self.assertEqual(('Legacy', 7), (row['name'], row['quantity_available']))
            os.environ.pop('STORE_DB_PATH', None)


if __name__ == '__main__':
    unittest.main()
