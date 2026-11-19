"""SQLite connection and first-run schema migration (no ORM)."""
from contextlib import contextmanager
from pathlib import Path
import os
import sqlite3

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "database" / "schema.sql"


def database_path() -> Path:
    return Path(os.environ.get("STORE_DB_PATH", str(ROOT / "database" / "store.db")))


@contextmanager
def get_connection(write: bool = False):
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=20, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    try:
        if write:
            # Prevent two buyers from simultaneously checking the final unit.
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if write:
            conn.commit()
    except Exception:
        if write:
            conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        # Compatibility with the user's earlier products(name, price_toman, stock) table.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
        if "description" not in columns:
            conn.execute("ALTER TABLE products ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        if "active" not in columns:
            conn.execute("ALTER TABLE products ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        # Legacy users.role has a restrictive CHECK; add a separate account_type
        # so existing users/orders keep their IDs and references intact.
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "account_type" not in user_columns:
            conn.execute("""ALTER TABLE users ADD COLUMN account_type TEXT NOT NULL
                            DEFAULT 'customer' CHECK (account_type IN ('customer', 'seller'))""")
        if "shop_id" not in columns:
            conn.execute("ALTER TABLE products ADD COLUMN shop_id INTEGER REFERENCES shops(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_products_shop ON products(shop_id)")
        legacy_stock = "stock" in columns

    with get_connection(write=True) as conn:
        # Existing shop owners become sellers; never modify administrator privileges.
        conn.execute("""
            UPDATE users SET account_type='seller'
            WHERE role != 'admin' AND id IN (SELECT user_id FROM shops)
              AND account_type != 'seller'
        """)
        # Backfill fulfillment rows for orders placed before this upgrade.
        conn.execute("""
            INSERT OR IGNORE INTO shop_fulfillments(order_id, shop_id)
            SELECT DISTINCT oi.order_id, p.shop_id
              FROM order_items AS oi JOIN products AS p ON p.id=oi.product_id
             WHERE p.shop_id IS NOT NULL
        """)
        if legacy_stock:
            conn.execute("""
                INSERT OR IGNORE INTO inventory(product_id, quantity_available, quantity_reserved)
                SELECT id, MAX(0, COALESCE(stock, 0)), 0 FROM products
            """)
