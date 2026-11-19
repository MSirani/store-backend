# Store Backend

**A multi-vendor e-commerce REST API built with FastAPI, SQLite, and plain SQL.**

Store Backend is a backend-first project for a marketplace where sellers manage their own shops and customers can browse products, place orders, and pay with an in-app wallet.

I built it to work through the parts of an online store that aren't obvious from the product page: keeping inventory accurate, preventing one seller from accessing another seller's data, and making sure a checkout either completes correctly or leaves the database unchanged.

The project deliberately uses **raw SQL instead of an ORM**. Queries, relationships, and transactions are written explicitly so the database behavior is easy to follow.

> **Project status:** Learning project / local MVP. The payment gateway is simulated for development; this is not a production-ready commerce platform.

## What it can do

- **Separate seller and customer accounts.** Each account type has its own registration and login endpoints. Administrator accounts cannot be created through public registration.
- **Seller-owned shops.** A seller can create a shop, publish products, update prices, adjust inventory, and manage shipping for their own sales.
- **Product catalog.** Customers can browse products and categories and see current availability.
- **Shopping cart and checkout.** Add products, update quantities, choose a delivery address, and place an order.
- **Wallet and payments.** New accounts receive a configurable-in-code demo signup credit of **100,000 toman**. Checkout supports wallet, pending gateway, and mixed-payment flows.
- **Order history.** Product prices are saved at checkout so older orders aren't affected by later price changes.
- **Multi-shop order handling.** Each seller sees only their own order items. Shipping details are exposed to the relevant seller after payment, and each shop tracks its shipping status independently.

## Tech stack

| Component | Technology |
| --- | --- |
| API | Python, FastAPI |
| Validation | Pydantic |
| Database | SQLite (`sqlite3`) |
| Database access | Parameterized SQL queries — **no ORM** |
| Authentication | Password hashing with `scrypt`, bearer-token sessions |
| Tests | Python `unittest`, HTTPX |

## Getting started

You'll need **Python 3.12+** and Git.

```bash
git clone https://github.com/MSirani/store-backend.git
cd store-backend
```

Create a virtual environment and install dependencies.

**Windows (PowerShell)**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000/docs** to explore and test the API through Swagger UI.

The SQLite database is created automatically at `database/store.db` on the first run. No PostgreSQL server or separate database configuration is needed.

> **Already have local data?** Back up `database/store.db` before replacing project files or updating the schema. The application includes compatibility logic for its earlier SQLite schema, but that is not a substitute for a backup.

## Try a complete flow

A simple way to explore the API is to use two browser profiles: one for a seller and another for a customer.

**Seller**

1. Register with `POST /auth/register/seller` and sign in with `POST /auth/login/seller`.
2. Copy the returned `access_token` into Swagger UI's **Authorize** dialog.
3. Create a shop using `POST /shops`:

   ```json
   { "name": "Tech Corner" }
   ```

4. Add a product using `POST /shops/me/products`:

   ```json
   {
     "name": "Logitech G102",
     "description": "Gaming mouse",
     "price_toman": 35000,
     "stock": 5
   }
   ```

**Customer**

1. Register with `POST /auth/register/customer`, sign in with `POST /auth/login/customer`, and authorize the new session.
2. Check the catalog with `GET /products` and note the product's actual ID.
3. Add a delivery address with `POST /addresses`.
4. Add an item with `POST /cart/items`:

   ```json
   { "product_id": 1, "quantity": 2 }
   ```

5. Check `GET /cart`, then place an order with `POST /orders`:

   ```json
   { "address_id": 1, "method": "wallet", "wallet_amount_toman": 0 }
   ```

6. Review `GET /orders`, `GET /wallet`, and the product's remaining stock. Back in the seller session, check `GET /shops/me/orders`.

*IDs in these examples are placeholders; use the IDs returned by your own requests. Wallet checkout succeeds only when the available balance covers the order total.*

## API overview

| Area | Example endpoints |
| --- | --- |
| Accounts | `POST /auth/register/customer`, `POST /auth/register/seller`, `POST /auth/login/customer`, `POST /auth/login/seller`, `GET /auth/me` |
| Shops | `POST /shops`, `GET /shops/me`, `GET /shops/me/products` |
| Seller products | `POST /shops/me/products`, `PATCH /shops/me/products/{product_id}`, `POST /shops/me/products/{product_id}/stock` |
| Catalog | `GET /products`, `GET /products/{product_id}`, `GET /categories` |
| Cart | `GET /cart`, `POST /cart/items`, `PATCH /cart/items/{product_id}`, `DELETE /cart/items/{product_id}` |
| Checkout | `POST /addresses`, `POST /orders`, `GET /orders`, `GET /orders/{order_id}` |
| Wallet | `GET /wallet`, `GET /wallet/transactions` |
| Seller fulfillment | `GET /shops/me/orders`, `PATCH /shops/me/orders/{order_id}/shipping` |

The full endpoint list, request schemas, and response types are available at `/docs` while the server is running.

## How the database is designed

The schema is organized around a few core relationships:

- `users` own carts, addresses, wallets, and orders.
- `shops` belong to sellers; products belong to a shop.
- `products` and `inventory` are separate, with one inventory record per product.
- `carts` contain `cart_items`; finalized orders contain `order_items` with **price snapshots**.
- `orders` have payment records, and `wallet_transactions` keep a ledger of wallet changes.
- `shop_fulfillments` track shipping separately for each shop represented in an order.

The editable database diagram is in [`database/erd.dbml`](database/erd.dbml), and the SQLite schema is in [`database/schema.sql`](database/schema.sql).

### Inventory and transaction safety

Adding an item to a cart does not reserve stock. Stock is checked at checkout, where SQLite's `BEGIN IMMEDIATE` and conditional updates prevent two concurrent checkouts from selling the same final unit. If part of an order fails, the transaction is rolled back.

This is one of the main reasons I chose to keep SQL visible in the code.

## Project structure

```text
app/
  main.py                 FastAPI application and router setup
  db.py                   SQLite connections, transactions, schema upgrades
  security.py             Authentication and session helpers
  schemas.py              Request validation
  routes/                 Accounts, shops, products, cart, orders, wallet, etc.
database/
  schema.sql              SQLite schema
  erd.dbml                Database relationship diagram
scripts/
  promote_admin.py        Local administrator setup helper
tests/                    Automated API and transaction tests
requirements.txt
```

## Run the tests

From the project root:

```bash
python -m unittest discover -s tests -v
```

On Windows, you can use `\.venv\Scripts\python.exe` in place of `python`.

The test suite uses temporary databases and covers account roles, seller ownership, order visibility, wallet payments, stock reservations, transaction rollback, and concurrent attempts to purchase the last available unit.

## Current limitations and next steps

This repository is intended for learning, local testing, and further development. It does **not** process real bank payments. Gateway and mixed payments remain pending unless a development-only settlement simulation is explicitly enabled. Do not enable that simulation on a public deployment.

Before turning it into a real store, I'd want to add a verified payment provider and callback flow, seller verification, rate limiting, password recovery, more complete refunds and shipping workflows, deployment hardening, and a proper migration strategy. PostgreSQL is also a planned next step for heavier multi-user workloads.

**Security note:** Never commit `.env` files, access tokens, passwords, or the local `database/store.db` file. The included `.gitignore` excludes the development database and common local artifacts.

---

Built as a hands-on exploration of API design, relational databases, and the small details that make e-commerce backends reliable.
