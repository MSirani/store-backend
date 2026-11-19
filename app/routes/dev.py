"""Manual fake payment settlement; disabled unless explicitly enabled for local development."""
import os
from fastapi import APIRouter, Depends, HTTPException

from ..db import get_connection
from ..schemas import DevPaymentResult
from ..security import require_admin
from .orders import complete_reserved_stock, release_reserved_stock

router = APIRouter(prefix="/dev", tags=["Development only"])


@router.post("/payments/{payment_id}/settle")
def settle_fake_gateway_payment(payment_id: int, data: DevPaymentResult,
                                _admin: dict = Depends(require_admin)):
    if os.environ.get("ENABLE_DEV_PAYMENT_SETTLEMENT", "0") != "1":
        raise HTTPException(404, "Development payment settlement is disabled")
    # Never expose this endpoint in production. This is NOT a real PSP callback.
    with get_connection(write=True) as conn:
        payment = conn.execute("""
            SELECT p.id, p.order_id, p.status, p.method,
                   o.user_id, o.status AS order_status, o.wallet_amount_toman
              FROM payments p JOIN orders o ON o.id = p.order_id WHERE p.id=?
        """, (payment_id,)).fetchone()
        if payment is None or payment["method"] != "gateway":
            raise HTTPException(404, "Gateway payment not found")
        if payment["status"] != "pending" or payment["order_status"] != "pending_payment":
            raise HTTPException(409, "Payment already settled or order not pending")
        order_id = payment["order_id"]
        held = payment["wallet_amount_toman"]
        if data.success:
            if held:
                wallet = conn.execute("SELECT id FROM wallets WHERE user_id=?",
                                      (payment["user_id"],)).fetchone()
                cur = conn.execute("""
                    UPDATE wallets SET balance_toman=balance_toman-?,
                                       reserved_toman=reserved_toman-?
                     WHERE user_id=? AND reserved_toman >= ?
                       AND balance_toman >= ?
                """, (held, held, payment["user_id"], held, held))
                if cur.rowcount != 1:
                    raise RuntimeError("Inconsistent held wallet funds")
                conn.execute("""
                    INSERT INTO wallet_transactions(wallet_id, order_id, kind, amount_toman, event_key)
                    VALUES (?, ?, 'purchase', ?, ?)
                """, (wallet["id"], order_id, -held, f"order:{order_id}:debit"))
                conn.execute("""
                    UPDATE payments SET status='paid'
                     WHERE order_id=? AND method='wallet' AND status='pending'
                """, (order_id,))
            complete_reserved_stock(conn, order_id)
            conn.execute("UPDATE payments SET status='paid' WHERE id=?", (payment_id,))
            conn.execute("UPDATE orders SET status='paid' WHERE id=?", (order_id,))
            result = 'paid'
        else:
            release_reserved_stock(conn, order_id)
            if held:
                conn.execute("""
                    UPDATE wallets SET reserved_toman=reserved_toman-?
                     WHERE user_id=? AND reserved_toman>=?
                """, (held, payment["user_id"], held))
            conn.execute("UPDATE payments SET status='failed' WHERE id=?", (payment_id,))
            conn.execute("""
                UPDATE payments SET status='cancelled'
                 WHERE order_id=? AND method='wallet' AND status='pending'
            """, (order_id,))
            conn.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
            result = 'cancelled'
    return {"payment_id": payment_id, "order_id": order_id, "order_status": result}
