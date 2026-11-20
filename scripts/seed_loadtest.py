"""Create a *new* isolated SQLite load-test database and synthetic fixtures.

Usage from store-backend root (with STORE_DB_PATH set to database/loadtest.db):
    python -m scripts.seed_loadtest
Refuses to touch database/store.db or an existing loadtest.db.
"""

from pathlib import Path

from app.db import ROOT, database_path, get_connection, init_db
from app.security import hash_password

LOAD_DB = (ROOT / "database" / "loadtest.db").resolve()
MAIN_DB = (ROOT / "database" / "store.db").resolve()


def seed_user(conn, name, email, password, account_type="customer", role="customer"):
    user_id = conn.execute(
        "INSERT INTO users(name,email,password_hash,account_type,role) VALUES (?,?,?,?,?)",
        (name, email, hash_password(password), account_type, role),
    ).lastrowid
    conn.execute("INSERT INTO carts(user_id) VALUES (?)", (user_id,))
    conn.execute("INSERT INTO wallets(user_id) VALUES (?)", (user_id,))
    return user_id


def main():
    current = database_path().resolve()
    if current != LOAD_DB or current == MAIN_DB:
        raise SystemExit("REFUSED: STORE_DB_PATH must be exactly database/loadtest.db")
    if LOAD_DB.exists() or Path(str(LOAD_DB) + "-wal").exists() or Path(str(LOAD_DB) + "-shm").exists():
        raise SystemExit("REFUSED: load-test DB already exists. Never overwrite existing data.")
    init_db()
    with get_connection(write=True) as conn:
        seller = seed_user(conn, "LOADTEST Seed Seller", "loadtest-seller@example.com",
                           "SyntheticSeller-2026!", account_type="seller")
        seed_user(conn, "LOADTEST Admin", "loadtest-admin@example.com",
                  "SyntheticAdmin-2026!", role="admin")
        shop_id = conn.execute(
            "INSERT INTO shops(user_id,name) VALUES (?,?)", (seller, "LOADTEST-SeedShop")
        ).lastrowid
        def create(name, price, quantity):
            pid = conn.execute(
                "INSERT INTO products(name,description,price_toman,shop_id) VALUES (?,?,?,?)",
                (name, "Synthetic testing only", price, shop_id),
            ).lastrowid
            conn.execute(
                "INSERT INTO inventory(product_id,quantity_available) VALUES (?,?)",
                (pid, quantity),
            )
            return pid
        assert create("LOADTEST-BUY", 1000, 1_000_000) == 1
        # Price zero guarantees insufficient STOCK is reported before wallet issues.
        assert create("LOADTEST-EMPTY", 0, 0) == 2
        assert create("LOADTEST-EXPENSIVE", 200_000, 1_000_000) == 3
        cid = conn.execute(
            "INSERT INTO categories(name) VALUES ('LOADTEST-Category')"
        ).lastrowid
        assert cid == 1
        for i in range(4, 84):
            pid = create(f"LOADTEST-Product-{i:03d}", 2000 + 100 * (i % 5), 100_000)
            assert pid == i
            if i % 2 == 0:
                conn.execute("INSERT INTO product_categories(product_id,category_id) VALUES (?,?)",
                             (pid, cid))
    print("Created an isolated SQLite DB:", LOAD_DB)
    print("Products: LOADTEST-BUY=1, LOADTEST-EMPTY=2, LOADTEST-EXPENSIVE=3, catalog=4..83")
    print("Synthetic seller/admin accounts created (see load_tests/README.md)")
    print("Never use loadtest.db outside local testing.")


if __name__ == "__main__":
    main()
