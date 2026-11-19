from fastapi import APIRouter, Depends
from ..db import get_connection
from ..schemas import AddressInput
from ..security import current_user

router = APIRouter(prefix="/addresses", tags=["Addresses"])


@router.get("")
def get_addresses(user: dict = Depends(current_user)):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, label, recipient_name, phone, city, street, postal_code
            FROM addresses WHERE user_id=? ORDER BY id
        """, (user["id"],)).fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
def create_address(data: AddressInput, user: dict = Depends(current_user)):
    with get_connection(write=True) as conn:
        cur = conn.execute("""
            INSERT INTO addresses(user_id, label, recipient_name, phone, city, street, postal_code)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user["id"], *data.model_dump().values()))
        address_id = cur.lastrowid
    return {"id": address_id, **data.model_dump()}
