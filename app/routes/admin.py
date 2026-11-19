"""Admin-only manual wallet credits; NOT a gateway integration."""
import sqlite3
from fastapi import APIRouter, Depends, HTTPException

from ..db import get_connection
from ..schemas import AdminWalletCredit
from ..security import require_admin

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.post("/wallets/{user_id}/credit")
def credit_wallet(user_id: int, data: AdminWalletCredit,
                  _admin: dict = Depends(require_admin)):
    # Administrative offline credit for testing/manual reconciliation only.
    try:
        with get_connection(write=True) as conn:
            wallet = conn.execute("SELECT id FROM wallets WHERE user_id=?", (user_id,)).fetchone()
            if wallet is None:
                raise HTTPException(404, "Wallet not found")
            # The UNIQUE event key ensures this credit can only be applied once.
            conn.execute("""
                INSERT INTO wallet_transactions(wallet_id, kind, amount_toman, event_key)
                VALUES (?, 'topup', ?, ?)
            """, (wallet["id"], data.amount_toman, data.event_key))
            conn.execute("UPDATE wallets SET balance_toman=balance_toman+? WHERE id=?",
                         (data.amount_toman, wallet["id"]))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Credit with this event_key already exists") from exc
    return {"user_id": user_id, "credited_toman": data.amount_toman,
            "event_key": data.event_key}
