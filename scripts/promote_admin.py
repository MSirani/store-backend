"""Local command only. Run after registering your user: python -m scripts.promote_admin email@example.com"""
import sys
from app.db import init_db, get_connection


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m scripts.promote_admin YOUR_REGISTERED_EMAIL")
    email = sys.argv[1].strip().lower()
    init_db()
    with get_connection(write=True) as conn:
        cursor = conn.execute("UPDATE users SET role='admin' WHERE email=?", (email,))
        if cursor.rowcount != 1:
            raise SystemExit("User not found. Register via /auth/register first.")
    print("Admin role granted to:", email)


if __name__ == "__main__":
    main()
