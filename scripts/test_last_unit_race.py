"""HTTP concurrency check: two buyers race to purchase a one-unit SKU.

Run ONLY against the disposable, local FastAPI instance after seed_loadtest.
This is a one-off correctness/stress probe, not a Locust throughput benchmark.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

HOST = os.environ.get("LOADTEST_HOST", "http://127.0.0.1:8001").rstrip("/")


def main():
    if (urlsplit(HOST).hostname not in {"127.0.0.1", "localhost", "::1"}
            or os.getenv("LOADTEST_ALLOW_WRITES") != "YES"):
        raise SystemExit("REFUSED: run only against localhost with LOADTEST_ALLOW_WRITES=YES")

    def request(method, path, expected=200, **kwargs):
        with httpx.Client(timeout=30) as client:
            response = client.request(method, HOST + path, **kwargs)
        if response.status_code != expected:
            raise AssertionError(f"{method} {path}: expected HTTP {expected}, got {response.status_code}: {response.text[:150]}")
        return response.json() if response.content else {}

    product1 = request("GET", "/products/1")
    if not product1.get("name", "").startswith("LOADTEST-BUY"):
        raise SystemExit("REFUSED: expected seeded LOADTEST product")
    seller = request("POST", "/auth/login/seller", json={
        "email": "loadtest-seller@example.com", "password": "SyntheticSeller-2026!"})
    sh = {"Authorization": "Bearer " + seller["access_token"]}
    product = request("POST", "/shops/me/products", 201, headers=sh, json={
        "name": "LOADTEST-RACE-" + uuid4().hex[:8],
        "description": "Last unit only", "price_toman": 1000, "stock": 1})
    pid = product["id"]
    buyers = []
    for _ in range(2):
        email = f"loadtest-race-{uuid4().hex}@example.com"
        request("POST", "/auth/register/customer", 201, json={
            "name": "Race Buyer", "email": email, "password": "SyntheticBuyer-2026!"})
        login = request("POST", "/auth/login/customer", json={
            "email": email, "password": "SyntheticBuyer-2026!"})
        h = {"Authorization": "Bearer " + login["access_token"]}
        addr = request("POST", "/addresses", 201, headers=h, json={
            "label": "Race", "recipient_name": "Test", "phone": "09120000000",
            "city": "Test", "street": "Test", "postal_code": "0000000000"})
        request("POST", "/cart/items", 201, headers=h,
                json={"product_id": pid, "quantity": 1})
        buyers.append((h, addr["id"]))

    barrier = Barrier(2)
    def buy(info):
        headers, addr_id = info
        barrier.wait(timeout=10)
        with httpx.Client(timeout=40) as client:
            r = client.post(HOST + "/orders", headers=headers,
                            json={"address_id": addr_id, "method": "wallet",
                                  "wallet_amount_toman": 0})
        return r.status_code, r.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(buy, buyers))
    codes = sorted(status for status, _ in results)
    inventory = request("GET", f"/products/{pid}")
    assert codes == [201, 409], results
    assert inventory["quantity_available"] == 0, inventory
    assert inventory["quantity_reserved"] == 0, inventory
    paid = [body for status, body in results if status == 201][0]
    denied = [body for status, body in results if status == 409][0]
    assert paid["status"] == "paid", paid
    assert "Insufficient stock" in denied.get("detail", ""), denied
    orders_per_buyer = [request("GET", "/orders", headers=h) for h, _ in buyers]
    assert sorted(map(len, orders_per_buyer)) == [0, 1], orders_per_buyer
    print("PASS: exactly one buyer paid; other received 409; inventory=0 and no oversell")


if __name__ == "__main__":
    main()
