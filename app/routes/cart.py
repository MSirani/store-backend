from fastapi import APIRouter, Depends, HTTPException

from ..db import get_connection
from ..schemas import CartItemInput, CartQuantityInput
from ..security import current_user

router = APIRouter(prefix="/cart", tags=["Cart"])


def ensure_cart(conn, user_id: int) -> int:
    conn.execute("INSERT OR IGNORE INTO carts(user_id) VALUES (?)", (user_id,))
    return conn.execute("SELECT id FROM carts WHERE user_id=?", (user_id,)).fetchone()["id"]


@router.get("")
def get_cart(user: dict = Depends(current_user)):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT ci.product_id, p.name, ci.quantity, p.price_toman,
                   ci.quantity * p.price_toman AS subtotal_toman,
                   COALESCE(i.quantity_available, 0) AS quantity_available
              FROM cart_items ci
              JOIN carts c ON c.id = ci.cart_id
              JOIN products p ON p.id = ci.product_id
              LEFT JOIN inventory i ON i.product_id = p.id
             WHERE c.user_id=? ORDER BY ci.product_id
        """, (user["id"],)).fetchall()
    items = [dict(row) for row in rows]
    return {"items": items, "total_toman": sum(item["subtotal_toman"] for item in items)}


@router.post("/items", status_code=201)
def add_item(data: CartItemInput, user: dict = Depends(current_user)):
    with get_connection(write=True) as conn:
        product = conn.execute("SELECT active FROM products WHERE id=?",
                               (data.product_id,)).fetchone()
        if product is None or not product["active"]:
            raise HTTPException(404, "Active product not found")
        cart_id = ensure_cart(conn, user["id"])
        existing = conn.execute("SELECT quantity FROM cart_items WHERE cart_id=? AND product_id=?",
                                (cart_id, data.product_id)).fetchone()
        if existing and existing["quantity"] + data.quantity > 100:
            raise HTTPException(422, "Maximum quantity per product is 100")
        conn.execute("""
            INSERT INTO cart_items(cart_id, product_id, quantity) VALUES (?, ?, ?)
            ON CONFLICT(cart_id, product_id)
            DO UPDATE SET quantity = cart_items.quantity + excluded.quantity
        """, (cart_id, data.product_id, data.quantity))
        quantity = conn.execute("SELECT quantity FROM cart_items WHERE cart_id=? AND product_id=?",
                                (cart_id, data.product_id)).fetchone()["quantity"]
    return {"product_id": data.product_id, "quantity": quantity}


@router.patch("/items/{product_id}")
def update_item(product_id: int, data: CartQuantityInput,
                user: dict = Depends(current_user)):
    with get_connection(write=True) as conn:
        cur = conn.execute("""
            UPDATE cart_items SET quantity = ?
             WHERE product_id=? AND cart_id IN (SELECT id FROM carts WHERE user_id=?)
        """, (data.quantity, product_id, user["id"]))
        if cur.rowcount != 1:
            raise HTTPException(404, "Cart item not found")
    return {"product_id": product_id, "quantity": data.quantity}


@router.delete("/items/{product_id}", status_code=204)
def remove_item(product_id: int, user: dict = Depends(current_user)):
    with get_connection(write=True) as conn:
        cur = conn.execute("""
            DELETE FROM cart_items WHERE product_id=?
             AND cart_id IN (SELECT id FROM carts WHERE user_id=?)
        """, (product_id, user["id"]))
        if cur.rowcount != 1:
            raise HTTPException(404, "Cart item not found")
    return None
