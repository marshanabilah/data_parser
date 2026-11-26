"""Transactional storage for dashboard shop settings and confirmed orders."""

import sqlite3
import json
import re
from typing import Optional, Sequence

from dashboard_orders import OrderRecord, STATUSES

DEFAULT_SHOPS = {"Mercari": False, "Rakuten": False, "Fril": False, "UMS": True, "Lashinbang": True, "Weverse": True}


def valid_jpy(value):
    return bool(re.fullmatch(r"\s*(?:¥|￥|JPY\s*)?\d[\d,]*(?:\s*円)?\s*", value, re.IGNORECASE))


class DashboardStore:
    def __init__(self, path: str):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS shops (name TEXT PRIMARY KEY COLLATE NOCASE, allows_multiple INTEGER NOT NULL, shipping_jpy INTEGER);
                CREATE TABLE IF NOT EXISTS order_batches (token TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS dashboard_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_token TEXT NOT NULL, order_date TEXT NOT NULL, line_name TEXT NOT NULL, dn_line TEXT NOT NULL, shop TEXT NOT NULL, shipping_jpy INTEGER, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dashboard_products (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES dashboard_orders(id), name TEXT NOT NULL, link TEXT NOT NULL, price_jpy INTEGER NOT NULL, quantity INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS imported_orders (id INTEGER PRIMARY KEY, original_json TEXT NOT NULL, shop TEXT NOT NULL DEFAULT '', nominal TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS dashboard_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            db.executemany("INSERT OR IGNORE INTO shops VALUES (?, ?, NULL)", DEFAULT_SHOPS.items())

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def shops(self):
        with self.connect() as db:
            return db.execute("SELECT name, allows_multiple, shipping_jpy FROM shops ORDER BY name").fetchall()

    def exchange_rate(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM dashboard_settings WHERE key = 'jpy_idr_rate'").fetchone()
            return row[0] if row else None

    def set_exchange_rate(self, rate):
        from pricing import parse_rate
        value = str(parse_rate(rate))
        with self.connect() as db:
            db.execute("INSERT INTO dashboard_settings VALUES ('jpy_idr_rate', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (value,))

    def set_shop(self, name: str, allows_multiple: bool, shipping_jpy: Optional[int]):
        if not name.strip() or type(allows_multiple) is not bool or (shipping_jpy is not None and (type(shipping_jpy) is not int or shipping_jpy < 0)):
            raise ValueError("Invalid shop settings")
        with self.connect() as db:
            db.execute("INSERT INTO shops VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET allows_multiple=excluded.allows_multiple, shipping_jpy=excluded.shipping_jpy", (name.strip(), allows_multiple, shipping_jpy))

    def save(self, token: str, records: Sequence[OrderRecord], new_shop=None):
        if not token or len(token) > 200 or not records:
            raise ValueError("A confirmation token and records are required")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM order_batches WHERE token = ?", (token,)).fetchone():
                return [row[0] for row in db.execute("SELECT id FROM dashboard_orders WHERE batch_token = ? ORDER BY id", (token,))]
            if new_shop is not None:
                name, allows_multiple = new_shop
                if not name.strip() or len(name) > 100 or type(allows_multiple) is not bool:
                    raise ValueError("Invalid shop settings")
                db.execute("INSERT OR IGNORE INTO shops VALUES (?, ?, NULL)", (name.strip(), allows_multiple))
            db.execute("INSERT INTO order_batches VALUES (?)", (token,))
            ids = []
            for order in records:
                setting = db.execute("SELECT allows_multiple FROM shops WHERE name = ?", (order.shop,)).fetchone()
                if not setting or (not setting[0] and len(order.products) != 1) or order.status != "Purchased":
                    raise ValueError("Invalid shop or order status")
                cursor = db.execute("INSERT INTO dashboard_orders (batch_token, order_date, line_name, dn_line, shop, shipping_jpy, status) VALUES (?, ?, ?, ?, ?, ?, ?)", (token, order.order_date.isoformat(), order.line_name, order.dn_line, order.shop, order.shipping_jpy, order.status))
                ids.append(cursor.lastrowid)
                db.executemany("INSERT INTO dashboard_products (order_id, name, link, price_jpy, quantity) VALUES (?, ?, ?, ?, ?)", [(cursor.lastrowid, p.name, p.link, p.price_jpy, p.quantity) for p in order.products])
            return ids

    def import_orders(self, records):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in records:
                order_id = int(row["Order No."])
                if db.execute("SELECT 1 FROM dashboard_orders WHERE id = ? UNION ALL SELECT 1 FROM imported_orders WHERE id = ?", (order_id, order_id)).fetchone():
                    raise ValueError(f"Item ID {order_id} already exists; no rows were imported.")
            db.executemany("INSERT INTO imported_orders (id, original_json, nominal) VALUES (?, ?, ?)", [(int(row["Order No."]), json.dumps(row, ensure_ascii=False), row["Nominal"]) for row in records])
            # Reserve imported IDs so future dashboard-created IDs cannot collide.
            maximum = max(int(row["Order No."]) for row in records)
            db.execute("INSERT INTO sqlite_sequence (name, seq) SELECT 'dashboard_orders', ? WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'dashboard_orders')", (maximum,))
            db.execute("UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = 'dashboard_orders'", (maximum,))

    def imported(self, query=""):
        with self.connect() as db:
            rows = db.execute("SELECT id, original_json, shop, nominal FROM imported_orders ORDER BY id DESC").fetchall()
        result = []
        for order_id, raw, shop, nominal in rows:
            row = json.loads(raw)
            if not query or query == str(order_id) or query.casefold() in row["DN Line"].casefold():
                result.append((order_id, row, shop, nominal))
        return result

    def update_imported(self, order_id, shop, nominal):
        if len(shop) > 100 or len(nominal) > 100:
            raise ValueError("Shop or price is too long")
        if nominal.strip() and not valid_jpy(nominal):
            raise ValueError("Price must be a whole-yen amount or blank")
        with self.connect() as db:
            return db.execute("UPDATE imported_orders SET shop = ?, nominal = ? WHERE id = ?", (shop.strip(), nominal.strip(), order_id)).rowcount == 1

    def search(self, query: str = ""):
        with self.connect() as db:
            return db.execute("SELECT id, order_date, line_name, dn_line, shop, shipping_jpy, status FROM dashboard_orders WHERE CAST(id AS TEXT) = ? OR instr(lower(line_name), lower(?)) > 0 OR instr(lower(dn_line), lower(?)) > 0 ORDER BY id DESC", (query, query, query)).fetchall()

    def products(self, order_id: int):
        with self.connect() as db:
            return db.execute("SELECT name, link, price_jpy, quantity FROM dashboard_products WHERE order_id = ? ORDER BY id", (order_id,)).fetchall()

    def has_link(self, link: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM dashboard_products WHERE link = ? LIMIT 1", (link,)).fetchone() is not None

    def set_status(self, order_id: int, status: str):
        if status not in STATUSES:
            raise ValueError("Invalid status")
        with self.connect() as db:
            return db.execute("UPDATE dashboard_orders SET status = ? WHERE id = ?", (status, order_id)).rowcount == 1
