"""Seller-scoped shop APIs. No ORM; ownership enforced in SQL."""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_connection
from ..schemas import ProductInput, ProductPatch, ShopInput, ShippingStatusInput, StockAdjustment
from ..security import current_user

router = APIRouter(prefix="/shops", tags=["Shops (Sellers)"])


def require_shop(user: dict = Depends(current_user)) -> dict:
    if user["account_type"] != "seller":
        raise HTTPException(403, "Seller account required")
    if user["shop_id"] is None:
        raise HTTPException(403, "Create a shop before using seller APIs")
    return user


def product_for_shop(conn, shop_id: int, product_id: int) -> dict:
    row = conn.execute("""
        SELECT p.id, p.name, p.description, p.price_toman, p.active, p.shop_id,
               i.quantity_available, i.quantity_reserved
          FROM products AS p JOIN inventory AS i ON i.product_id = p.id
         WHERE p.id=? AND p.shop_id=?
    """, (product_id, shop_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Product not found in your shop")
    return dict(row)


@router.post("", status_code=201)
def create_shop(data: ShopInput, user: dict = Depends(current_user)):
    if user["account_type"] != "seller":
        raise HTTPException(403, "Seller account required")
    if user["shop_id"] is not None:
        raise HTTPException(409, "You already have a shop")
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(422, "Shop name is too short")
    try:
        with get_connection(write=True) as conn:
            row = conn.execute("INSERT INTO shops(user_id, name) VALUES (?, ?)",
                               (user["id"], name))
            shop_id = row.lastrowid
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "You already have a shop") from exc
    return {"id": shop_id, "user_id": user["id"], "name": name}


@router.get("/me")
def my_shop(user: dict = Depends(require_shop)):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM shops WHERE id=? AND user_id=?",
                           (user["shop_id"], user["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "Shop not found")
    return dict(row)


@router.get("/me/products")
def my_products(user: dict = Depends(require_shop)):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT p.id, p.name, p.description, p.price_toman, p.active,
                   p.shop_id, i.quantity_available, i.quantity_reserved
              FROM products AS p JOIN inventory AS i ON i.product_id = p.id
             WHERE p.shop_id=? ORDER BY p.id
        """, (user["shop_id"],)).fetchall()
    return [dict(row) for row in rows]


@router.post("/me/products", status_code=201)
def create_shop_product(data: ProductInput, user: dict = Depends(require_shop)):
    with get_connection(write=True) as conn:
        cur = conn.execute("""
            INSERT INTO products(name, description, price_toman, shop_id)
            VALUES (?, ?, ?, ?)
        """, (data.name.strip(), data.description, data.price_toman, user["shop_id"]))
        product_id = cur.lastrowid
        conn.execute("INSERT INTO inventory(product_id, quantity_available) VALUES (?, ?)",
                     (product_id, data.stock))
        product = product_for_shop(conn, user["shop_id"], product_id)
    return product


@router.patch("/me/products/{product_id}")
def update_shop_product(product_id: int, data: ProductPatch,
                        user: dict = Depends(require_shop)):
    fields = data.model_dump(exclude_unset=True)
    if not fields or any(value is None for value in fields.values()):
        raise HTTPException(422, "Provide one or more non-null fields")
    if "active" in fields:
        fields["active"] = int(fields["active"])
    # Keys come only from the fixed ProductPatch model; not untrusted SQL identifiers.
    assignments = ", ".join(f"{field}=?" for field in fields)
    with get_connection(write=True) as conn:
        cur = conn.execute(f"""
            UPDATE products SET {assignments} WHERE id=? AND shop_id=?
        """, (*fields.values(), product_id, user["shop_id"]))
        if cur.rowcount != 1:
            raise HTTPException(404, "Product not found in your shop")
        product = product_for_shop(conn, user["shop_id"], product_id)
    return product


@router.post("/me/products/{product_id}/stock")
def update_shop_stock(product_id: int, data: StockAdjustment,
                      user: dict = Depends(require_shop)):
    with get_connection(write=True) as conn:
        cur = conn.execute("""
            UPDATE inventory SET quantity_available=quantity_available + ?
             WHERE product_id=? AND quantity_available + ? >= 0
               AND EXISTS (SELECT 1 FROM products
                            WHERE id=? AND shop_id=?)
        """, (data.delta, product_id, data.delta, product_id, user["shop_id"]))
        if cur.rowcount != 1:
            raise HTTPException(409, "Product missing, not yours, or insufficient available stock")
        product = product_for_shop(conn, user["shop_id"], product_id)
    return product


@router.delete("/me/products/{product_id}", status_code=204)
def deactivate_shop_product(product_id: int, user: dict = Depends(require_shop)):
    with get_connection(write=True) as conn:
        cur = conn.execute("UPDATE products SET active=0 WHERE id=? AND shop_id=?",
                           (product_id, user["shop_id"]))
        if cur.rowcount != 1:
            raise HTTPException(404, "Product not found in your shop")
    return None


@router.get("/me/orders")
def my_order_items(user: dict = Depends(require_shop)):
    """Only this shop's order lines; shipping details shown only for paid orders."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT o.id AS order_id, o.status AS order_status, o.created_at,
                   CASE WHEN o.status='paid' THEN u.id END AS buyer_id,
                   CASE WHEN o.status='paid' THEN u.name END AS buyer_name,
                   CASE WHEN o.status='paid' THEN a.recipient_name END AS recipient_name,
                   CASE WHEN o.status='paid' THEN a.phone END AS recipient_phone,
                   CASE WHEN o.status='paid' THEN a.city END AS shipping_city,
                   CASE WHEN o.status='paid' THEN a.street END AS shipping_street,
                   CASE WHEN o.status='paid' THEN a.postal_code END AS shipping_postal_code,
                   CASE WHEN o.status='paid' THEN COALESCE(sf.status, 'processing')
                        WHEN o.status='cancelled' THEN 'cancelled'
                        ELSE 'awaiting_payment' END AS shipping_status,
                   oi.product_id, p.name AS product_name, oi.quantity,
                   oi.unit_price_toman,
                   oi.quantity * oi.unit_price_toman AS subtotal_toman
              FROM order_items AS oi
              JOIN orders AS o ON o.id=oi.order_id
              JOIN products AS p ON p.id=oi.product_id
              JOIN users AS u ON u.id=o.user_id
              JOIN addresses AS a ON a.id=o.address_id AND a.user_id=o.user_id
              LEFT JOIN shop_fulfillments AS sf
                ON sf.order_id=o.id AND sf.shop_id=p.shop_id
             WHERE p.shop_id=? ORDER BY o.id DESC, oi.id
        """, (user["shop_id"],)).fetchall()
    return [dict(row) for row in rows]


@router.patch("/me/orders/{order_id}/shipping")
def update_shipping(order_id: int, data: ShippingStatusInput,
                    user: dict = Depends(require_shop)):
    """Per-shop shipping progress; seller cannot modify another shop's items."""
    with get_connection(write=True) as conn:
        row = conn.execute("""
            SELECT o.status AS order_status, sf.status AS shipping_status
              FROM shop_fulfillments AS sf
              JOIN orders AS o ON o.id=sf.order_id
             WHERE sf.order_id=? AND sf.shop_id=?
        """, (order_id, user["shop_id"])).fetchone()
        if row is None:
            raise HTTPException(404, "Order not found in your shop")
        if row["order_status"] != "paid":
            raise HTTPException(409, "Cannot ship an unpaid or cancelled order")
        valid_next = {"processing": "shipped", "shipped": "delivered"}
        if valid_next.get(row["shipping_status"]) != data.status:
            raise HTTPException(409, "Invalid shipping status transition")
        conn.execute("""
            UPDATE shop_fulfillments
               SET status=?, updated_at=CURRENT_TIMESTAMP
             WHERE order_id=? AND shop_id=?
        """, (data.status, order_id, user["shop_id"]))
    return {"order_id": order_id, "shop_id": user["shop_id"], "shipping_status": data.status}
