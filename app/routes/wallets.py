from fastapi import APIRouter, Depends
from ..db import get_connection
from ..security import current_user

router = APIRouter(prefix="/wallet", tags=["Wallet"])


@router.get("")
def get_wallet(user: dict = Depends(current_user)):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT id, balance_toman, reserved_toman,
                   balance_toman - reserved_toman AS spendable_toman
            FROM wallets WHERE user_id=?
        """, (user["id"],)).fetchone()
    return dict(row)


@router.get("/transactions")
def get_transactions(user: dict = Depends(current_user)):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT wt.id, wt.order_id, wt.kind, wt.amount_toman, wt.event_key, wt.created_at
              FROM wallet_transactions wt
              JOIN wallets w ON w.id = wt.wallet_id
             WHERE w.user_id=? ORDER BY wt.id DESC
        """, (user["id"],)).fetchall()
    return [dict(row) for row in rows]
