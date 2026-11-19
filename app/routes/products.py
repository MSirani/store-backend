import sqlite3
from fastapi import APIRouter, Depends, HTTPException

from ..db import get_connection
from ..schemas import CategoryInput, ProductInput, ProductPatch, StockAdjustment
from ..security import require_admin

router = APIRouter(tags=["Products & Categories"])


@router.get("/products")
def list_products(category_id: int | None = None):
    where = "WHERE p.active = 1"
    params = []
    if category_id is not None:
        where += " AND EXISTS (SELECT 1 FROM product_categories pc WHERE pc.product_id=p.id AND pc.category_id=?)"
        params.append(category_id)
    with get_connection() as conn:
        rows = conn.execute(f"""
            SELECT p.id, p.name, p.description, p.price_toman, p.active,
                   p.shop_id,
                   COALESCE(i.quantity_available, 0) AS quantity_available,
                   COALESCE(i.quantity_reserved, 0) AS quantity_reserved
              FROM products p LEFT JOIN inventory i ON i.product_id = p.id
              {where} ORDER BY p.id
        """, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/products/{product_id}")
def get_product(product_id: int):
    with get_connection() as conn:
        row = conn.execute("""
            SELECT p.id, p.name, p.description, p.price_toman, p.active,
                   p.shop_id,
                   COALESCE(i.quantity_available, 0) AS quantity_available,
                   COALESCE(i.quantity_reserved, 0) AS quantity_reserved
              FROM products p LEFT JOIN inventory i ON i.product_id = p.id
             WHERE p.id = ?
        """, (product_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Product not found")
        categories = conn.execute("""
            SELECT c.id, c.name FROM categories c
            JOIN product_categories pc ON pc.category_id = c.id
            WHERE pc.product_id = ? ORDER BY c.name
        """, (product_id,)).fetchall()
    return {**dict(row), "categories": [dict(c) for c in categories]}


@router.post("/products", status_code=201)
def create_product(data: ProductInput, _admin: dict = Depends(require_admin)):
    with get_connection(write=True) as conn:
        cur = conn.execute("""
            INSERT INTO products(name, description, price_toman) VALUES (?, ?, ?)
        """, (data.name.strip(), data.description, data.price_toman))
        product_id = cur.lastrowid
        conn.execute("INSERT INTO inventory(product_id, quantity_available) VALUES (?, ?)",
                     (product_id, data.stock))
    return get_product(product_id)


@router.patch("/products/{product_id}")
def patch_product(product_id: int, data: ProductPatch, _admin: dict = Depends(require_admin)):
    fields = data.model_dump(exclude_unset=True)
    if not fields or any(value is None for value in fields.values()):
        raise HTTPException(422, "Provide one or more non-null fields")
    # Column names only come from a fixed Pydantic model, never arbitrary user keys.
    if "active" in fields:
        fields["active"] = int(fields["active"])
    columns = ", ".join(f"{column} = ?" for column in fields)
    with get_connection(write=True) as conn:
        cur = conn.execute(f"UPDATE products SET {columns} WHERE id = ?",
                           (*fields.values(), product_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Product not found")
    return get_product(product_id)


@router.delete("/products/{product_id}", status_code=204)
def deactivate_product(product_id: int, _admin: dict = Depends(require_admin)):
    # Soft delete: preserve old orders and item history.
    with get_connection(write=True) as conn:
        cur = conn.execute("UPDATE products SET active = 0 WHERE id = ?", (product_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Product not found")
    return None


@router.post("/products/{product_id}/stock")
def adjust_stock(product_id: int, data: StockAdjustment,
                 _admin: dict = Depends(require_admin)):
    with get_connection(write=True) as conn:
        cur = conn.execute("""
            UPDATE inventory SET quantity_available = quantity_available + ?
             WHERE product_id = ? AND quantity_available + ? >= 0
        """, (data.delta, product_id, data.delta))
        if cur.rowcount != 1:
            raise HTTPException(409, "Product missing or stock would become negative")
    return get_product(product_id)


@router.get("/categories")
def list_categories():
    with get_connection() as conn:
        rows = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    return [dict(row) for row in rows]


@router.post("/categories", status_code=201)
def create_category(data: CategoryInput, _admin: dict = Depends(require_admin)):
    try:
        with get_connection(write=True) as conn:
            cur = conn.execute("INSERT INTO categories(name) VALUES (?)", (data.name.strip(),))
            category_id = cur.lastrowid
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Category already exists") from exc
    return {"id": category_id, "name": data.name.strip()}


@router.post("/products/{product_id}/categories/{category_id}", status_code=201)
def assign_category(product_id: int, category_id: int,
                    _admin: dict = Depends(require_admin)):
    try:
        with get_connection(write=True) as conn:
            product = conn.execute("SELECT 1 FROM products WHERE id=?", (product_id,)).fetchone()
            category = conn.execute("SELECT 1 FROM categories WHERE id=?", (category_id,)).fetchone()
            if not product or not category:
                raise HTTPException(404, "Product or category not found")
            conn.execute("INSERT OR IGNORE INTO product_categories(product_id, category_id) VALUES (?, ?)",
                         (product_id, category_id))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Cannot assign category") from exc
    return {"product_id": product_id, "category_id": category_id}
