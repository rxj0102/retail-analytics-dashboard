"""
Load the cleaned CSVs from data/processed/ into a SQLite database at
data/retail.db with a relational schema (primary keys, foreign keys and
indexes on join and filter columns).

The database is rebuilt from scratch on every run so it always matches the
processed CSVs.

Usage:
    python src/build_database.py
"""

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
DB_PATH = ROOT / "data" / "retail.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE stores (
    store_id    TEXT PRIMARY KEY,
    store_name  TEXT NOT NULL,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL CHECK (length(state) = 2),
    region      TEXT NOT NULL,
    open_date   TEXT
);

CREATE TABLE products (
    product_id    TEXT PRIMARY KEY,
    product_name  TEXT NOT NULL,
    category      TEXT NOT NULL,
    base_price    REAL NOT NULL CHECK (base_price >= 0)
);

CREATE TABLE customers (
    customer_id        TEXT PRIMARY KEY,
    first_name         TEXT NOT NULL,
    last_name          TEXT NOT NULL,
    email              TEXT,
    email_missing      INTEGER NOT NULL DEFAULT 0 CHECK (email_missing IN (0, 1)),
    signup_date        TEXT,
    signup_date_valid  INTEGER NOT NULL DEFAULT 1 CHECK (signup_date_valid IN (0, 1)),
    state              TEXT CHECK (length(state) = 2),
    region             TEXT
);

CREATE TABLE orders (
    order_id     TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers (customer_id),
    store_id     TEXT NOT NULL REFERENCES stores (store_id),
    order_date   TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('completed', 'refunded', 'cancelled'))
);

CREATE TABLE order_items (
    order_item_id       TEXT PRIMARY KEY,
    order_id            TEXT NOT NULL REFERENCES orders (order_id),
    product_id          TEXT NOT NULL REFERENCES products (product_id),
    quantity            INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_at_sale  REAL NOT NULL CHECK (unit_price_at_sale >= 0),
    line_revenue        REAL NOT NULL
);

CREATE INDEX idx_orders_customer    ON orders (customer_id);
CREATE INDEX idx_orders_store       ON orders (store_id);
CREATE INDEX idx_orders_date        ON orders (order_date);
CREATE INDEX idx_orders_status      ON orders (status);
CREATE INDEX idx_items_order        ON order_items (order_id);
CREATE INDEX idx_items_product      ON order_items (product_id);
CREATE INDEX idx_customers_region   ON customers (region);
CREATE INDEX idx_stores_region      ON stores (region);
CREATE INDEX idx_products_category  ON products (category);
"""

# Load parents before children so foreign keys are satisfied.
TABLES = ["stores", "products", "customers", "orders", "order_items"]

INTEGER_COLUMNS = {"email_missing", "signup_date_valid", "quantity"}
REAL_COLUMNS = {"base_price", "unit_price_at_sale", "line_revenue"}


def convert(column, value):
    if value == "":
        return None
    if column in INTEGER_COLUMNS:
        return int(float(value))
    if column in REAL_COLUMNS:
        return float(value)
    return value


def load_table(conn, table):
    path = PROCESSED_DIR / f"{table}.csv"
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        rows = [tuple(convert(c, row[c]) for c in columns) for row in reader]
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", rows
    )
    return len(rows)


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        counts = {t: load_table(conn, t) for t in TABLES}
        conn.commit()

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"Foreign key violations found: {violations[:5]}")
        conn.execute("VACUUM")
    finally:
        conn.close()

    print(f"Built {DB_PATH.relative_to(ROOT)}")
    for table, n in counts.items():
        print(f"  {table:<12} {n:>7,} rows")
    print("  foreign key check: passed")


if __name__ == "__main__":
    main()
