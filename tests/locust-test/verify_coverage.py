"""Check that the load-test plan explicitly includes every application API route.

This is a *static* coverage checklist, not proof every path succeeded at runtime.
"""

from app.main import app

PLANNED = {
    ("GET", "/health"): "CatalogVisitor",
    ("POST", "/auth/register/customer"): "BuyerJourney, AdminJourney, SellerJourney",
    ("POST", "/auth/register/seller"): "SellerJourney",
    ("POST", "/auth/register"): "LegacyCompatibility (hidden)",
    ("POST", "/auth/login/customer"): "BuyerJourney, AdminJourney, SellerJourney",
    ("POST", "/auth/login/seller"): "SellerJourney, BuyerJourney (negative)",
    ("POST", "/auth/login/admin"): "AdminJourney",
    ("POST", "/auth/login"): "LegacyCompatibility (hidden)",
    ("GET", "/auth/me"): "BuyerJourney, SellerJourney, AdminJourney",
    ("POST", "/auth/logout"): "BuyerJourney, LegacyCompatibility",
    ("GET", "/products"): "CatalogVisitor, BuyerJourney, SellerJourney",
    ("GET", "/products/{product_id}"): "CatalogVisitor, BuyerJourney, SellerJourney",
    ("POST", "/products"): "AdminJourney, BuyerJourney (negative)",
    ("PATCH", "/products/{product_id}"): "AdminJourney",
    ("DELETE", "/products/{product_id}"): "AdminJourney",
    ("POST", "/products/{product_id}/stock"): "AdminJourney",
    ("GET", "/categories"): "CatalogVisitor",
    ("POST", "/categories"): "AdminJourney",
    ("POST", "/products/{product_id}/categories/{category_id}"): "AdminJourney",
    ("POST", "/shops"): "SellerJourney, BuyerJourney (negative)",
    ("GET", "/shops/me"): "SellerJourney",
    ("GET", "/shops/me/products"): "SellerJourney",
    ("POST", "/shops/me/products"): "SellerJourney",
    ("PATCH", "/shops/me/products/{product_id}"): "SellerJourney",
    ("POST", "/shops/me/products/{product_id}/stock"): "SellerJourney",
    ("DELETE", "/shops/me/products/{product_id}"): "SellerJourney",
    ("GET", "/shops/me/orders"): "SellerJourney",
    ("PATCH", "/shops/me/orders/{order_id}/shipping"): "SellerJourney",
    ("GET", "/cart"): "BuyerJourney",
    ("POST", "/cart/items"): "BuyerJourney, SellerJourney, AdminJourney",
    ("PATCH", "/cart/items/{product_id}"): "BuyerJourney",
    ("DELETE", "/cart/items/{product_id}"): "BuyerJourney",
    ("GET", "/addresses"): "BuyerJourney",
    ("POST", "/addresses"): "BuyerJourney, SellerJourney, AdminJourney",
    ("GET", "/wallet"): "BuyerJourney, AdminJourney",
    ("GET", "/wallet/transactions"): "BuyerJourney, AdminJourney",
    ("POST", "/orders"): "BuyerJourney, SellerJourney, AdminJourney",
    ("GET", "/orders"): "BuyerJourney",
    ("GET", "/orders/{order_id}"): "BuyerJourney, AdminJourney",
    ("POST", "/orders/{order_id}/cancel"): "BuyerJourney",
    ("POST", "/dev/payments/{payment_id}/settle"): "AdminJourney (synthetic only)",
    ("POST", "/admin/wallets/{user_id}/credit"): "AdminJourney",
}


def main():
    actual = {(method, route.path) for route in app.routes
              if route.path not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
              for method in route.methods if method not in {"HEAD", "OPTIONS"}}
    missing = actual - PLANNED.keys()
    stale = PLANNED.keys() - actual
    print(f"API routes: {len(actual)}; planned: {len(PLANNED)}")
    for key in sorted(actual):
        print("  ", f"{key[0]:6} {key[1]:57}", PLANNED.get(key, "*** MISSING ***"))
    if missing:
        print("MISSING:", sorted(missing))
    if stale:
        print("STALE:", sorted(stale))
    raise SystemExit(1 if missing or stale else 0)


if __name__ == "__main__":
    main()
