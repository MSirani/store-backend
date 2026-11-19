-- SQLite schema, raw SQL; foreign keys enabled by app/db.py.
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'admin')),
    account_type TEXT NOT NULL DEFAULT 'customer' CHECK (account_type IN ('customer', 'seller')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS addresses (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    recipient_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    city TEXT NOT NULL,
    street TEXT NOT NULL,
    postal_code TEXT NOT NULL
);

-- Each seller may own one shop (a user can still buy as a customer).
-- This table avoids changing the CHECK constraint on existing users.role columns.
CREATE TABLE IF NOT EXISTS shops (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL CHECK (length(trim(name)) >= 2),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price_toman INTEGER NOT NULL CHECK (price_toman >= 0),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    shop_id INTEGER REFERENCES shops(id)
);

CREATE TABLE IF NOT EXISTS inventory (
    product_id INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    quantity_available INTEGER NOT NULL DEFAULT 0 CHECK (quantity_available >= 0),
    quantity_reserved INTEGER NOT NULL DEFAULT 0 CHECK (quantity_reserved >= 0)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS product_categories (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, category_id)
);

CREATE TABLE IF NOT EXISTS carts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS cart_items (
    cart_id INTEGER NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (cart_id, product_id)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    address_id INTEGER NOT NULL REFERENCES addresses(id),
    status TEXT NOT NULL CHECK (status IN ('pending_payment', 'paid', 'cancelled')),
    total_toman INTEGER NOT NULL CHECK (total_toman >= 0),
    wallet_amount_toman INTEGER NOT NULL CHECK (wallet_amount_toman >= 0),
    gateway_amount_toman INTEGER NOT NULL CHECK (gateway_amount_toman >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_toman INTEGER NOT NULL CHECK (unit_price_toman >= 0),
    UNIQUE (order_id, product_id)
);

-- Fulfillment belongs to (order, shop), not the whole multi-shop order.
CREATE TABLE IF NOT EXISTS shop_fulfillments (
    order_id INTEGER NOT NULL REFERENCES orders(id),
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    status TEXT NOT NULL DEFAULT 'processing'
      CHECK (status IN ('processing', 'shipped', 'delivered')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (order_id, shop_id)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    method TEXT NOT NULL CHECK (method IN ('wallet', 'gateway')),
    amount_toman INTEGER NOT NULL CHECK (amount_toman >= 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'failed', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wallets (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    balance_toman INTEGER NOT NULL DEFAULT 0 CHECK (balance_toman >= 0),
    reserved_toman INTEGER NOT NULL DEFAULT 0 CHECK (reserved_toman >= 0 AND reserved_toman <= balance_toman)
);

CREATE TABLE IF NOT EXISTS wallet_transactions (
    id INTEGER PRIMARY KEY,
    wallet_id INTEGER NOT NULL REFERENCES wallets(id),
    order_id INTEGER REFERENCES orders(id),
    kind TEXT NOT NULL CHECK (kind IN ('signup_bonus', 'purchase', 'refund', 'topup')),
    amount_toman INTEGER NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_fulfillments_shop ON shop_fulfillments(shop_id);
CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_wallet_transactions_wallet ON wallet_transactions(wallet_id);
