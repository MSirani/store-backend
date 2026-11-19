from fastapi import APIRouter, Depends, HTTPException

from ..db import get_connection
from ..schemas import CheckoutInput
from ..security import current_user

router = APIRouter(prefix="/orders", tags=["Orders & Payments"])


def release_reserved_stock(conn, order_id: int) -> None:
    # Undo an unpaid order; physical stock becomes available again.
    items = conn.execute("SELECT product_id, quantity FROM order_items WHERE order_id=?",
                         (order_id,)).fetchall()
    for item in items:
        cur = conn.execute("""
            UPDATE inventory
               SET quantity_available = quantity_available + ?,
                   quantity_reserved = quantity_reserved - ?
             WHERE product_id = ? AND quantity_reserved >= ?
        """, (item["quantity"], item["quantity"], item["product_id"], item["quantity"]))
        if cur.rowcount != 1:
            raise RuntimeError("Inconsistent inventory during release")


def complete_reserved_stock(conn, order_id: int) -> None:
    # Units were already removed from available at checkout; now leave reserved.
    items = conn.execute("SELECT product_id, quantity FROM order_items WHERE order_id=?",
                         (order_id,)).fetchall()
    for item in items:
        cur = conn.execute("""
            UPDATE inventory SET quantity_reserved = quantity_reserved - ?
             WHERE product_id = ? AND quantity_reserved >= ?
        """, (item["quantity"], item["product_id"], item["quantity"]))
        if cur.rowcount != 1:
            raise RuntimeError("Inconsistent inventory during completion")


def order_details(conn, order_id: int, user_id: int) -> dict:
    order = conn.execute("SELECT * FROM orders WHERE id=? AND user_id=?",
                         (order_id, user_id)).fetchone()
    if not order:
        raise HTTPException(404, "Order not found")
    items = conn.execute("""
        SELECT oi.product_id, p.name, oi.quantity, oi.unit_price_toman,
               oi.quantity * oi.unit_price_toman AS subtotal_toman
          FROM order_items oi JOIN products p ON p.id=oi.product_id
         WHERE oi.order_id=? ORDER BY oi.id
    """, (order_id,)).fetchall()
    payments = conn.execute("""
        SELECT id, method, amount_toman, status, created_at
        FROM payments WHERE order_id=? ORDER BY id
    """, (order_id,)).fetchall()
    return {**dict(order), "items": [dict(r) for r in items],
            "payments": [dict(r) for r in payments]}


@router.post("", status_code=201)
def checkout(data: CheckoutInput, user: dict = Depends(current_user)):
    with get_connection(write=True) as conn:
        address = conn.execute("SELECT 1 FROM addresses WHERE id=? AND user_id=?",
                               (data.address_id, user["id"])).fetchone()
        if address is None:
            raise HTTPException(404, "Address not found for this user")

        # Price always comes from products, not the request body.
        items = conn.execute("""
            SELECT ci.product_id, ci.quantity, p.price_toman, p.active
              FROM cart_items ci
              JOIN carts c ON c.id=ci.cart_id
              JOIN products p ON p.id=ci.product_id
             WHERE c.user_id=? ORDER BY ci.product_id
        """, (user["id"],)).fetchall()
        if not items:
            raise HTTPException(400, "Cart is empty")
        if any(not item["active"] for item in items):
            raise HTTPException(409, "Cart contains an inactive product")
        total = sum(item["quantity"] * item["price_toman"] for item in items)

        if data.method == "wallet":
            if data.wallet_amount_toman != 0:
                raise HTTPException(422, "Do not set wallet_amount_toman for wallet-only checkout")
            wallet_due = total
        elif data.method == "gateway":
            if data.wallet_amount_toman != 0:
                raise HTTPException(422, "Gateway-only checkout requires wallet_amount_toman = 0")
            wallet_due = 0
        else:
            wallet_due = data.wallet_amount_toman
            if not 0 < wallet_due < total:
                raise HTTPException(422, "Mixed checkout requires a partial wallet amount")
        gateway_due = total - wallet_due

        wallet = conn.execute("SELECT id, balance_toman, reserved_toman FROM wallets WHERE user_id=?",
                              (user["id"],)).fetchone()
        if wallet is None or wallet["balance_toman"] - wallet["reserved_toman"] < wallet_due:
            raise HTTPException(409, "Not enough spendable wallet balance")

        # BEGIN IMMEDIATE protects the check and update as one serialized writer transaction.
        for item in items:
            stock_update = conn.execute("""
                UPDATE inventory
                   SET quantity_available = quantity_available - ?,
                       quantity_reserved = quantity_reserved + ?
                 WHERE product_id = ? AND quantity_available >= ?
            """, (item["quantity"], item["quantity"], item["product_id"], item["quantity"]))
            if stock_update.rowcount != 1:
                raise HTTPException(409, f"Insufficient stock for product {item['product_id']}")

        status = "pending_payment" if gateway_due else "paid"
        order = conn.execute("""
            INSERT INTO orders(user_id, address_id, status, total_toman,
                               wallet_amount_toman, gateway_amount_toman)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user["id"], data.address_id, status, total, wallet_due, gateway_due))
        order_id = order.lastrowid
        for item in items:
            conn.execute("""
                INSERT INTO order_items(order_id, product_id, quantity, unit_price_toman)
                VALUES (?, ?, ?, ?)
            """, (order_id, item["product_id"], item["quantity"], item["price_toman"]))

        # One independent shipment state for each shop in this order.
        conn.execute("""
            INSERT OR IGNORE INTO shop_fulfillments(order_id, shop_id)
            SELECT DISTINCT oi.order_id, p.shop_id
              FROM order_items AS oi JOIN products AS p ON p.id=oi.product_id
             WHERE oi.order_id=? AND p.shop_id IS NOT NULL
        """, (order_id,))

        if wallet_due:
            if gateway_due:
                # Mixed checkout: hold wallet funds until a verified gateway success.
                cur = conn.execute("""
                    UPDATE wallets SET reserved_toman = reserved_toman + ?
                     WHERE user_id=? AND balance_toman - reserved_toman >= ?
                """, (wallet_due, user["id"], wallet_due))
            else:
                # Fully prepaid wallet purchase: debit once, within the same transaction.
                cur = conn.execute("""
                    UPDATE wallets SET balance_toman = balance_toman - ?
                     WHERE user_id=? AND balance_toman - reserved_toman >= ?
                """, (wallet_due, user["id"], wallet_due))
                conn.execute("""
                    INSERT INTO wallet_transactions(wallet_id, order_id, kind, amount_toman, event_key)
                    VALUES (?, ?, 'purchase', ?, ?)
                """, (wallet["id"], order_id, -wallet_due, f"order:{order_id}:debit"))
            if cur.rowcount != 1:
                raise HTTPException(409, "Not enough wallet funds")
            conn.execute("""
                INSERT INTO payments(order_id, method, amount_toman, status)
                VALUES (?, 'wallet', ?, ?)
            """, (order_id, wallet_due, 'pending' if gateway_due else 'paid'))

        if gateway_due:
            conn.execute("""
                INSERT INTO payments(order_id, method, amount_toman, status)
                VALUES (?, 'gateway', ?, 'pending')
            """, (order_id, gateway_due))
        else:
            complete_reserved_stock(conn, order_id)

        conn.execute("""
            DELETE FROM cart_items WHERE cart_id IN (SELECT id FROM carts WHERE user_id=?)
        """, (user["id"],))

    with get_connection() as conn:
        return order_details(conn, order_id, user["id"])


@router.get("")
def list_orders(user: dict = Depends(current_user)):
    with get_connection() as conn:
        orders = conn.execute("""
            SELECT id, status, total_toman, wallet_amount_toman,
                   gateway_amount_toman, created_at
              FROM orders WHERE user_id=? ORDER BY id DESC
        """, (user["id"],)).fetchall()
    return [dict(r) for r in orders]


@router.get("/{order_id}")
def get_order(order_id: int, user: dict = Depends(current_user)):
    with get_connection() as conn:
        return order_details(conn, order_id, user["id"])


@router.post("/{order_id}/cancel")
def cancel_unpaid_order(order_id: int, user: dict = Depends(current_user)):
    with get_connection(write=True) as conn:
        order = conn.execute("SELECT * FROM orders WHERE id=? AND user_id=?",
                             (order_id, user["id"])).fetchone()
        if order is None:
            raise HTTPException(404, "Order not found")
        if order["status"] != "pending_payment":
            raise HTTPException(409, "Only unpaid orders can be cancelled here")
        release_reserved_stock(conn, order_id)
        wallet_held = order["wallet_amount_toman"]
        if wallet_held:
            cur = conn.execute("""
                UPDATE wallets SET reserved_toman = reserved_toman - ?
                 WHERE user_id=? AND reserved_toman >= ?
            """, (wallet_held, user["id"], wallet_held))
            if cur.rowcount != 1:
                raise RuntimeError("Inconsistent wallet reservation")
        conn.execute("UPDATE payments SET status='cancelled' WHERE order_id=? AND status='pending'",
                     (order_id,))
        conn.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
    return {"id": order_id, "status": "cancelled"}
