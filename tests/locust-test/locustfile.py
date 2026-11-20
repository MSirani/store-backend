"""Realistic, isolated Locust scenarios for Store Backend v0.1.0.

This file intentionally covers both correct business failures and happy paths.
All user classes require local HTTP and a seeded LOADTEST- catalog; all writes
additionally require LOADTEST_ALLOW_WRITES=YES. NEVER run against real data.
"""

import os
import random
from urllib.parse import urlsplit
from uuid import uuid4

from locust import HttpUser, between, task
from locust.exception import StopUser

TIMEOUT = 20
BUY_ID = int(os.getenv("LOADTEST_BUY_PRODUCT_ID", "1"))
EMPTY_ID = int(os.getenv("LOADTEST_EMPTY_PRODUCT_ID", "2"))
EXPENSIVE_ID = int(os.getenv("LOADTEST_EXPENSIVE_PRODUCT_ID", "3"))
SELLER_EMAIL = "loadtest-seller@example.com"
SELLER_PASSWORD = "SyntheticSeller-2026!"
ADMIN_EMAIL = "loadtest-admin@example.com"
ADMIN_PASSWORD = "SyntheticAdmin-2026!"
CUSTOMER_PASSWORD = "SyntheticBuyer-2026!"


class StoreUser(HttpUser):
    """HTTP helper: expected 4xx are successes; unexpected 2xx are failures too."""

    abstract = True
    wait_time = between(1, 3)
    write_enabled = False

    def on_start(self):
        hostname = urlsplit(self.host).hostname
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("Refusing non-local target; use an isolated local database")
        if self.write_enabled and os.getenv("LOADTEST_ALLOW_WRITES") != "YES":
            raise RuntimeError("Set LOADTEST_ALLOW_WRITES=YES only for a disposable database")
        for product_id, prefix in ((BUY_ID, "LOADTEST-BUY"),
                                   (EMPTY_ID, "LOADTEST-EMPTY"),
                                   (EXPENSIVE_ID, "LOADTEST-EXPENSIVE")):
            product = self.req("GET", f"/products/{product_id}", 200,
                               label="SETUP verify LOADTEST product")
            if not product or not str(product.get("name", "")).startswith(prefix):
                raise RuntimeError("Wrong/missing LOADTEST seed. Refusing requests")
        self.headers = {}

    def req(self, method, url, status=200, *, label=None, headers=None,
            json=None, contains=None, assert_field=None, validate=None):
        """Validate status, optional expected error detail and business response.

        Every request appears in Locust statistics under a stable name.
        Avoid logging tokens or request bodies in failure messages.
        """
        expected = {status} if isinstance(status, int) else set(status)
        with self.client.request(method, url, name=label or f"{method} {url}",
                                 headers=headers, json=json, timeout=TIMEOUT,
                                 catch_response=True) as response:
            if response.status_code not in expected:
                response.failure(f"Unexpected HTTP {response.status_code}; expected {sorted(expected)}")
                return None
            try:
                payload = response.json() if response.content else {}
            except ValueError:
                response.failure("Expected JSON response")
                return None
            if contains and contains.lower() not in str(payload.get("detail", "")).lower():
                response.failure("Unexpected business error detail")
                return None
            if assert_field:
                key, value = assert_field
                if not isinstance(payload, dict) or payload.get(key) != value:
                    response.failure(f"Unexpected business result: {key}")
                    return None
            if validate is not None and not validate(payload):
                response.failure("Business invariant check failed")
                return None
            # Expected 4xx responses (e.g. out-of-stock) are correct business outcomes.
            response.success()
            return payload

    def client_auth(self, role, email, password):
        body = self.req("POST", f"/auth/login/{role}", 200,
                        label=f"POST /auth/login/{role}",
                        json={"email": email, "password": password})
        token = (body or {}).get("access_token")
        return {"Authorization": "Bearer " + token} if token else None

    def create_buyer(self):
        email = f"loadtest-{uuid4().hex}@example.com"
        user = self.req("POST", "/auth/register/customer", 201,
                        json={"name": "Load Test Buyer", "email": email,
                              "password": CUSTOMER_PASSWORD})
        if not user:
            return None
        headers = self.client_auth("customer", email, CUSTOMER_PASSWORD)
        if not headers:
            return None
        address = self.req("POST", "/addresses", 201, headers=headers,
                           json={"label": "Test", "recipient_name": "Load Test Buyer",
                                 "phone": "09120000000", "city": "Test City",
                                 "street": "Synthetic Street", "postal_code": "0000000000"})
        if not address:
            return None
        return {"user_id": user["id"], "email": email, "headers": headers,
                "address_id": address["id"]}

    def add_to_cart(self, headers, product_id, quantity=1):
        return self.req("POST", "/cart/items", 201, headers=headers,
                        json={"product_id": product_id, "quantity": quantity})

    def order(self, buyer, method="wallet", amount=0, status="paid"):
        return self.req("POST", "/orders", 201, headers=buyer["headers"],
                        json={"address_id": buyer["address_id"],
                              "method": method, "wallet_amount_toman": amount},
                        assert_field=("status", status))


class CatalogVisitor(StoreUser):
    """Mostly reads; suitable for 5 -> 20 -> 50 -> 100 users."""

    weight = 60

    @task(7)
    def catalog(self):
        self.req("GET", "/products", 200)

    @task(4)
    def detail(self):
        self.req("GET", f"/products/{random.randint(4, 83)}", 200,
                 label="GET /products/{product_id}")

    @task(2)
    def category(self):
        self.req("GET", "/categories", 200)
        self.req("GET", "/products?category_id=1", 200,
                 label="GET /products?category_id={id}")

    @task(1)
    def health(self):
        self.req("GET", "/health", 200, assert_field=("status", "ok"))


class BuyerJourney(StoreUser):
    """Real shopping including expected failures, cancellation and wallet debit."""

    weight = 15
    write_enabled = True

    def on_start(self):
        super().on_start()
        self.buyer = self.create_buyer()
        if not self.buyer:
            raise StopUser()
        h = self.buyer["headers"]
        self.req("GET", "/auth/me", headers=h)
        self.req("GET", "/wallet", headers=h)
        self.req("GET", "/wallet/transactions", headers=h)
        self.req("GET", "/addresses", headers=h)
        self.req("GET", "/cart", headers=h)

    def clear_item(self, product_id):
        self.req("DELETE", f"/cart/items/{product_id}", 204,
                 label="DELETE /cart/items/{product_id}", headers=self.buyer["headers"])

    def check_spendable(self):
        wallet = self.req("GET", "/wallet", headers=self.buyer["headers"])
        if not wallet:
            return False
        if wallet.get("spendable_toman", 0) < 5000:
            # Keep load data independent of a virtual user's dwindling balance.
            fresh = self.create_buyer()
            if not fresh:
                return False
            self.buyer = fresh
        return True

    @task(5)
    def wallet_purchase(self):
        if not self.check_spendable():
            return
        h = self.buyer["headers"]
        self.req("GET", "/products")
        self.req("GET", f"/products/{BUY_ID}", label="GET /products/{product_id}")
        if self.add_to_cart(h, BUY_ID) is None:
            return
        self.req("GET", "/cart", headers=h)
        purchase = self.order(self.buyer)
        if purchase:
            order_id = purchase["id"]
            self.req("GET", f"/orders/{order_id}", headers=h,
                     label="GET /orders/{order_id}", assert_field=("status", "paid"))
            self.req("GET", "/orders", headers=h)
            self.req("GET", "/wallet", headers=h)
            self.req("GET", "/wallet/transactions", headers=h)

    @task(2)
    def change_cart_and_abandon(self):
        h = self.buyer["headers"]
        if self.add_to_cart(h, BUY_ID) is None:
            return
        self.req("PATCH", f"/cart/items/{BUY_ID}", headers=h,
                 label="PATCH /cart/items/{product_id}", json={"quantity": 2})
        self.req("GET", "/cart", headers=h)
        self.clear_item(BUY_ID)
        self.req("GET", "/cart", headers=h)

    @task(2)
    def empty_stock_must_fail(self):
        h = self.buyer["headers"]
        if self.add_to_cart(h, EMPTY_ID) is None:
            return
        self.req("POST", "/orders", 409, headers=h,
                 label="POST /orders [EXPECTED 409 stock]", contains="Insufficient stock",
                 json={"address_id": self.buyer["address_id"], "method": "wallet",
                       "wallet_amount_toman": 0})
        self.clear_item(EMPTY_ID)

    @task(2)
    def insufficient_wallet_must_fail(self):
        h = self.buyer["headers"]
        if self.add_to_cart(h, EXPENSIVE_ID) is None:
            return
        self.req("POST", "/orders", 409, headers=h,
                 label="POST /orders [EXPECTED 409 wallet]", contains="Not enough",
                 json={"address_id": self.buyer["address_id"], "method": "wallet",
                       "wallet_amount_toman": 0})
        self.clear_item(EXPENSIVE_ID)

    @task(2)
    def pending_gateway_cancellation(self):
        h = self.buyer["headers"]
        if self.add_to_cart(h, BUY_ID) is None:
            return
        order = self.order(self.buyer, method="gateway", status="pending_payment")
        if not order:
            return
        oid = order["id"]
        self.req("GET", f"/orders/{oid}", headers=h, label="GET /orders/{order_id}")
        self.req("POST", f"/orders/{oid}/cancel", headers=h,
                 label="POST /orders/{order_id}/cancel", assert_field=("status", "cancelled"))
        self.req("GET", f"/orders/{oid}", headers=h, label="GET /orders/{order_id}",
                 assert_field=("status", "cancelled"))

    @task(1)
    def mixed_payment_cancel(self):
        if not self.check_spendable():
            return
        h = self.buyer["headers"]
        if self.add_to_cart(h, BUY_ID) is None:
            return
        order = self.order(self.buyer, method="mixed", amount=300,
                           status="pending_payment")
        if not order:
            return
        self.req("GET", "/wallet", headers=h)
        self.req("POST", f"/orders/{order['id']}/cancel", headers=h,
                 label="POST /orders/{order_id}/cancel", assert_field=("status", "cancelled"))
        self.req("GET", "/wallet", headers=h)

    @task(1)
    def forbidden_and_invalid(self):
        h = self.buyer["headers"]
        self.req("POST", "/shops", 403, headers=h,
                 label="POST /shops [EXPECTED 403 customer]", json={"name": "Illegal Shop"})
        self.req("POST", "/products", 403, headers=h,
                 label="POST /products [EXPECTED 403 nonadmin]",
                 json={"name": "Forbidden", "price_toman": 1000, "stock": 1})
        self.req("GET", "/auth/me", 401, label="GET /auth/me [EXPECTED 401]",
                 headers={"Authorization": "Bearer invalid-loadtest-token"})
        self.req("GET", "/orders/999999999", 404, headers=h,
                 label="GET /orders/{id} [EXPECTED 404]")
        self.req("POST", "/auth/login/seller", 401,
                 label="POST /auth/login/seller [EXPECTED 401 role]",
                 json={"email": self.buyer["email"], "password": CUSTOMER_PASSWORD})

    @task(1)
    def logout_relogin(self):
        h = self.buyer["headers"]
        if self.req("POST", "/auth/logout", 204, headers=h) is None:
            return
        self.req("GET", "/auth/me", 401, headers=h,
                 label="GET /auth/me [EXPECTED 401 logged out]")
        new_headers = self.client_auth("customer", self.buyer["email"], CUSTOMER_PASSWORD)
        if new_headers:
            self.buyer["headers"] = new_headers


class SellerJourney(StoreUser):
    """Seller creates/updates/deactivates own products and ships own orders."""

    weight = 4
    write_enabled = True

    def on_start(self):
        super().on_start()
        self.email = f"loadtest-seller-{uuid4().hex}@example.com"
        signup = self.req("POST", "/auth/register/seller", 201,
                          json={"name": "Load Test Seller", "email": self.email,
                                "password": SELLER_PASSWORD})
        if not signup:
            raise StopUser()
        self.headers = self.client_auth("seller", self.email, SELLER_PASSWORD)
        if not self.headers:
            raise StopUser()
        shop = self.req("POST", "/shops", 201, headers=self.headers,
                        json={"name": f"LOADTEST-Shop-{uuid4().hex[:8]}"})
        if not shop:
            raise StopUser()
        self.shop_id = shop["id"]
        product = self.create_product()
        if not product:
            raise StopUser()
        self.product_id = product["id"]

    def create_product(self):
        return self.req("POST", "/shops/me/products", 201, headers=self.headers,
                        json={"name": f"LOADTEST-Seller-Product-{uuid4().hex[:8]}",
                              "description": "Synthetic seller product",
                              "price_toman": 1000, "stock": 100_000})

    @task(4)
    def manage_inventory(self):
        h = self.headers
        self.req("GET", "/shops/me", headers=h)
        self.req("GET", "/shops/me/products", headers=h)
        self.req("GET", "/products", 200)
        self.req("GET", f"/products/{self.product_id}", 200,
                 label="GET /products/{product_id}")
        self.req("PATCH", f"/shops/me/products/{self.product_id}", headers=h,
                 label="PATCH /shops/me/products/{id}",
                 json={"description": "Load-tested product", "price_toman": 1000})
        self.req("POST", f"/shops/me/products/{self.product_id}/stock", headers=h,
                 label="POST /shops/me/products/{id}/stock", json={"delta": 2})
        self.req("POST", f"/shops/me/products/{self.product_id}/stock", 409, headers=h,
                 label="POST /shops/me/products/{id}/stock [EXPECTED 409]",
                 json={"delta": -999999999})

    @task(2)
    def product_lifecycle(self):
        product = self.create_product()
        if not product:
            return
        pid = product["id"]
        self.req("PATCH", f"/shops/me/products/{pid}", headers=self.headers,
                 label="PATCH /shops/me/products/{id}", json={"price_toman": 1200})
        self.req("DELETE", f"/shops/me/products/{pid}", 204, headers=self.headers,
                 label="DELETE /shops/me/products/{id}")
        self.req("GET", f"/products/{pid}", headers=self.headers,
                 label="GET /products/{product_id}", assert_field=("active", 0))

    @task(2)
    def fulfill_paid_order(self):
        buyer = self.create_buyer()
        if not buyer or self.add_to_cart(buyer["headers"], self.product_id) is None:
            return
        order = self.order(buyer)
        if not order:
            return
        oid = order["id"]
        own_lines = self.req(
            "GET", "/shops/me/orders", headers=self.headers,
            validate=lambda rows: isinstance(rows, list)
              and any(row["order_id"] == oid and row["product_id"] == self.product_id
                      for row in rows)
              and all(row["product_id"] != BUY_ID for row in rows))
        if own_lines is None:
            return
        self.req("PATCH", f"/shops/me/orders/{oid}/shipping", headers=self.headers,
                 label="PATCH /shops/me/orders/{id}/shipping",
                 json={"status": "shipped"}, assert_field=("shipping_status", "shipped"))
        self.req("PATCH", f"/shops/me/orders/{oid}/shipping", headers=self.headers,
                 label="PATCH /shops/me/orders/{id}/shipping",
                 json={"status": "delivered"}, assert_field=("shipping_status", "delivered"))
        self.req("PATCH", f"/shops/me/orders/{oid}/shipping", 409, headers=self.headers,
                 label="PATCH /shops/me/orders/{id}/shipping [EXPECTED 409]",
                 json={"status": "shipped"})

    @task(1)
    def ownership_and_duplicate_shop(self):
        h = self.headers
        self.req("POST", "/shops", 409, headers=h,
                 label="POST /shops [EXPECTED 409 duplicate]",
                 json={"name": "Duplicate Shop"})
        self.req("PATCH", f"/shops/me/products/{BUY_ID}", 404, headers=h,
                 label="PATCH /shops/me/products/{id} [EXPECTED 404 foreign]",
                 json={"price_toman": 999})
        self.req("POST", f"/shops/me/products/{BUY_ID}/stock", 409, headers=h,
                 label="POST /shops/me/products/{id}/stock [EXPECTED 409 foreign]",
                 json={"delta": 1})
        self.req("DELETE", f"/shops/me/products/{BUY_ID}", 404, headers=h,
                 label="DELETE /shops/me/products/{id} [EXPECTED 404 foreign]")
        self.req("POST", "/admin/wallets/1/credit", 403, headers=h,
                 label="POST /admin/wallets/{id}/credit [EXPECTED 403]",
                 json={"amount_toman": 1, "event_key": f"LOADTEST:{uuid4().hex}"})


class AdminJourney(StoreUser):
    """Admin catalog, credit, and simulated gateway success/failure ONLY."""

    weight = 1
    write_enabled = True

    def on_start(self):
        super().on_start()
        self.headers = self.client_auth("admin", ADMIN_EMAIL, ADMIN_PASSWORD)
        if not self.headers:
            raise StopUser()
        self.req("GET", "/auth/me", headers=self.headers)

    @task(3)
    def admin_product_category_lifecycle(self):
        h = self.headers
        category = self.req("POST", "/categories", 201, headers=h,
                            json={"name": f"LOADTEST-Category-{uuid4().hex[:10]}"})
        product = self.req("POST", "/products", 201, headers=h,
                           json={"name": f"LOADTEST-AdminProduct-{uuid4().hex[:10]}",
                                 "price_toman": 1000, "stock": 3})
        if not product:
            return
        pid = product["id"]
        self.req("PATCH", f"/products/{pid}", headers=h,
                 label="PATCH /products/{id}", json={"price_toman": 1100})
        self.req("POST", f"/products/{pid}/stock", headers=h,
                 label="POST /products/{id}/stock", json={"delta": 2})
        if category:
            cid = category["id"]
            self.req("POST", f"/products/{pid}/categories/{cid}", 201, headers=h,
                     label="POST /products/{id}/categories/{id}")
            self.req("GET", f"/products?category_id={cid}", 200,
                     label="GET /products?category_id={id}")
        self.req("DELETE", f"/products/{pid}", 204, headers=h,
                 label="DELETE /products/{id}")
        self.req("GET", f"/products/{pid}", 200,
                 label="GET /products/{product_id}", assert_field=("active", 0))

    @task(2)
    def credit_wallet_once(self):
        buyer = self.create_buyer()
        if not buyer:
            return
        path = f"/admin/wallets/{buyer['user_id']}/credit"
        event_key = f"LOADTEST:topup:{uuid4().hex}"
        data = {"amount_toman": 2000, "event_key": event_key}
        self.req("POST", path, 200, headers=self.headers,
                 label="POST /admin/wallets/{id}/credit", json=data)
        self.req("POST", path, 409, headers=self.headers,
                 label="POST /admin/wallets/{id}/credit [EXPECTED 409 duplicate]",
                 json=data)
        self.req("GET", "/wallet", headers=buyer["headers"])
        self.req("GET", "/wallet/transactions", headers=buyer["headers"])

    def settle_gateway(self, success, mixed=False):
        buyer = self.create_buyer()
        if not buyer or self.add_to_cart(buyer["headers"], BUY_ID) is None:
            return
        order = self.order(buyer, method="mixed" if mixed else "gateway",
                           amount=300 if mixed else 0, status="pending_payment")
        if not order:
            return
        gateway = next((p for p in order["payments"] if p["method"] == "gateway"), None)
        if not gateway:
            return
        payid = gateway["id"]
        result = "paid" if success else "cancelled"
        self.req("POST", f"/dev/payments/{payid}/settle", headers=self.headers,
                 label=("POST /dev/payments/{id}/settle "
                        + ("[SIMULATED SUCCESS]" if success else "[SIMULATED FAILURE]")),
                 json={"success": success}, assert_field=("order_status", result))
        self.req("GET", f"/orders/{order['id']}", headers=buyer["headers"],
                 label="GET /orders/{order_id}", assert_field=("status", result))
        self.req("GET", "/wallet", headers=buyer["headers"])
        self.req("GET", "/wallet/transactions", headers=buyer["headers"])
        self.req("POST", f"/dev/payments/{payid}/settle", 409, headers=self.headers,
                 label="POST /dev/payments/{id}/settle [EXPECTED 409 duplicate]",
                 json={"success": success})

    @task(2)
    def gateway_payment_success(self):
        self.settle_gateway(True)

    @task(2)
    def gateway_payment_failure(self):
        self.settle_gateway(False)

    @task(1)
    def mixed_payment_success(self):
        self.settle_gateway(True, mixed=True)

    @task(1)
    def mixed_payment_failure(self):
        self.settle_gateway(False, mixed=True)


class LegacyCompatibility(StoreUser):
    """Hidden compatibility auth routes; run only when measuring old clients."""

    weight = 1
    write_enabled = True

    @task
    def compatibility_flow(self):
        email = f"loadtest-legacy-{uuid4().hex}@example.com"
        self.req("POST", "/auth/register", 201,
                 json={"name": "Legacy Test User", "email": email,
                       "password": CUSTOMER_PASSWORD, "account_type": "customer"})
        login = self.req("POST", "/auth/login", 200,
                         json={"email": email, "password": CUSTOMER_PASSWORD})
        if login and login.get("access_token"):
            h = {"Authorization": "Bearer " + login["access_token"]}
            self.req("GET", "/auth/me", headers=h)
            self.req("POST", "/auth/logout", 204, headers=h)




#$env:LOADTEST_ALLOW_WRITES = 'YES'

#.\.venv\Scripts\python.exe -m locust -f tests\load_tests\locustfile.py --host http://127.0.0.1:8001