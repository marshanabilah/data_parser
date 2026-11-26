"""Transactional storage for dashboard shop settings and confirmed orders."""

import sqlite3
import json
import re
import hashlib
from typing import Optional, Sequence

from dashboard_orders import OrderRecord, STATUSES
from product_urls import extract_product_urls, single_https_host

DEFAULT_SHOPS = {"Mercari": False, "Rakuten": False, "Fril": False, "UMS": True, "Lashinbang": True, "Weverse": True}
DEFAULT_DOMAINS = {"mercari.com": "Mercari", "rakuten.co.jp": "Rakuten", "fril.jp": "Fril", "lashinbang.com": "Lashinbang"}


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
                CREATE TABLE IF NOT EXISTS csv_import_batches (fingerprint TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS shop_domains (domain TEXT PRIMARY KEY COLLATE NOCASE, shop TEXT NOT NULL REFERENCES shops(name));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(imported_orders)")}
            if "current_status" not in columns:
                db.execute("ALTER TABLE imported_orders ADD COLUMN current_status TEXT")
            if "assumed_year" not in columns:
                db.execute("ALTER TABLE imported_orders ADD COLUMN assumed_year INTEGER")
            db.executemany("INSERT OR IGNORE INTO shops VALUES (?, ?, NULL)", DEFAULT_SHOPS.items())
            if not db.execute("SELECT 1 FROM dashboard_settings WHERE key = 'shop_domains_initialized'").fetchone():
                db.executemany("INSERT OR IGNORE INTO shop_domains VALUES (?, ?)", DEFAULT_DOMAINS.items())
                db.execute("INSERT INTO dashboard_settings VALUES ('shop_domains_initialized', '1')")
            self._fill_missing_shops(db)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def shops(self):
        with self.connect() as db:
            return db.execute("SELECT name, allows_multiple, shipping_jpy FROM shops ORDER BY name").fetchall()

    def shop_domains(self):
        with self.connect() as db:
            return db.execute("SELECT domain, shop FROM shop_domains ORDER BY domain").fetchall()

    @staticmethod
    def _matching_shop(link, domains):
        host = single_https_host(link)
        if not host:
            return None
        matches = [(domain, shop) for domain, shop in domains if host == domain or host.endswith("." + domain)]
        return max(matches, key=lambda item: len(item[0]))[1] if matches else None

    def _fill_missing_shops(self, db):
        domains = db.execute("SELECT domain, shop FROM shop_domains").fetchall()
        rows = db.execute("SELECT id, original_json FROM imported_orders WHERE trim(shop) = ''").fetchall()
        for order_id, raw in rows:
            shop = self._matching_shop(json.loads(raw).get("Link", ""), domains)
            if shop:
                db.execute("UPDATE imported_orders SET shop = ? WHERE id = ? AND trim(shop) = ''", (shop, order_id))

    def _set_shop_domains(self, db, name, domains):
        normalized = []
        for domain in domains:
            domain = domain.strip().lower().rstrip(".")
            if not domain:
                continue
            if len(domain) > 253 or not re.fullmatch(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", domain):
                raise ValueError("Enter website domains such as example.com, without https:// or paths.")
            normalized.append(domain)
        if len(normalized) != len(set(normalized)):
            raise ValueError("A domain can only be listed once.")
        for domain in normalized:
            existing = db.execute("SELECT shop FROM shop_domains WHERE domain = ?", (domain,)).fetchone()
            if existing and existing[0].casefold() != name.casefold():
                raise ValueError("A website domain is already assigned to another shop.")
        db.execute("DELETE FROM shop_domains WHERE shop = ? COLLATE NOCASE", (name,))
        db.executemany("INSERT INTO shop_domains (domain, shop) VALUES (?, ?)", [(domain, name) for domain in normalized])

    def exchange_rate(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM dashboard_settings WHERE key = 'jpy_idr_rate'").fetchone()
            return row[0] if row else None

    def set_exchange_rate(self, rate):
        from pricing import parse_rate
        value = str(parse_rate(rate))
        with self.connect() as db:
            db.execute("INSERT INTO dashboard_settings VALUES ('jpy_idr_rate', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (value,))

    def set_shop(self, name: str, allows_multiple: bool, shipping_jpy: Optional[int], domains=None):
        if not name.strip() or type(allows_multiple) is not bool or (shipping_jpy is not None and (type(shipping_jpy) is not int or shipping_jpy < 0)):
            raise ValueError("Invalid shop settings")
        with self.connect() as db:
            db.execute("INSERT INTO shops VALUES (?, ?, ?) ON CONFLICT(name) DO UPDATE SET allows_multiple=excluded.allows_multiple, shipping_jpy=excluded.shipping_jpy", (name.strip(), allows_multiple, shipping_jpy))
            if domains is not None:
                self._set_shop_domains(db, name.strip(), domains)
                self._fill_missing_shops(db)

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

    def import_orders(self, records, assumed_year=None):
        if assumed_year is not None and (type(assumed_year) is not int or not 1 <= assumed_year <= 9999):
            raise ValueError("Invalid import year.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            fingerprint = hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if db.execute("SELECT 1 FROM csv_import_batches WHERE fingerprint = ?", (fingerprint,)).fetchone():
                raise ValueError("This CSV has already been imported; no rows were added.")
            imported_ids = {int(json.loads(row[0])["Order No."]) for row in db.execute("SELECT original_json FROM imported_orders")}
            overlapping = {int(row["Order No."]) for row in records} & imported_ids
            if overlapping:
                raise ValueError(f"This CSV overlaps with {len(overlapping)} previously imported original order numbers. No rows were added; review the overlap before importing.")
            existing = {row[0] for row in db.execute("SELECT id FROM dashboard_orders UNION SELECT id FROM imported_orders")}
            originals = {int(row["Order No."]) for row in records}
            maximum = max(existing | originals)
            assigned = set(existing)
            saved = []
            for row in records:
                original_id = int(row["Order No."])
                order_id = original_id
                if order_id in assigned:
                    maximum += 1
                    if maximum > 9223372036854775807:
                        raise ValueError("No available item IDs; no rows were imported.")
                    order_id = maximum
                assigned.add(order_id)
                saved.append((order_id, json.dumps(row, ensure_ascii=False), row["Nominal"], assumed_year))
            db.executemany("INSERT INTO imported_orders (id, original_json, nominal, assumed_year) VALUES (?, ?, ?, ?)", saved)
            self._fill_missing_shops(db)
            db.execute("INSERT INTO csv_import_batches VALUES (?)", (fingerprint,))
            # Reserve imported IDs so future dashboard-created IDs cannot collide.
            maximum = max(assigned)
            db.execute("INSERT INTO sqlite_sequence (name, seq) SELECT 'dashboard_orders', ? WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'dashboard_orders')", (maximum,))
            db.execute("UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = 'dashboard_orders'", (maximum,))
            return [item[0] for item in saved]

    def imported(self, query=""):
        with self.connect() as db:
            rows = db.execute("SELECT id, original_json, shop, nominal, current_status, assumed_year FROM imported_orders ORDER BY id DESC").fetchall()
        result = []
        for order_id, raw, shop, nominal, current_status, assumed_year in rows:
            row = json.loads(raw)
            row["_current_status"] = current_status
            row["_assumed_year"] = assumed_year
            if not query or query == str(order_id) or query.casefold() in row["DN Line"].casefold():
                result.append((order_id, row, shop, nominal))
        return result

    def update_imported(self, order_id, shop, nominal, new_rule=None, remember_domain=False, status=None):
        if len(shop) > 100 or len(nominal) > 100:
            raise ValueError("Shop or price is too long")
        if status and status not in STATUSES:
            raise ValueError("Invalid status")
        with self.connect() as db:
            record = db.execute("SELECT original_json, nominal, shop FROM imported_orders WHERE id = ?", (order_id,)).fetchone()
            if not record:
                return False
            if nominal.strip() != record[1].strip() and nominal.strip() and not valid_jpy(nominal):
                raise ValueError("Price must be a whole-yen amount or blank")
            name = shop.strip()
            known = db.execute("SELECT name FROM shops WHERE name = ?", (name,)).fetchone() if name else None
            if name and not known:
                if new_rule not in ("0", "1"):
                    if name.casefold() != record[2].strip().casefold():
                        raise ValueError("This is a new shop. Choose whether it allows one link or multiple links.")
                else:
                    db.execute("INSERT INTO shops (name, allows_multiple, shipping_jpy) VALUES (?, ?, NULL)", (name, int(new_rule)))
                    known = (name,)
            if known:
                name = known[0]
            if remember_domain and known:
                link = json.loads(record[0]).get("Link", "")
                domain = single_https_host(link)
                if not domain:
                    raise ValueError("This order does not have a single HTTPS website to remember.")
                domains = db.execute("SELECT domain, shop FROM shop_domains").fetchall()
                owner = self._matching_shop(link, domains)
                if owner and owner.casefold() != name.casefold():
                    raise ValueError("This website is already associated with another shop. Check Shop settings.")
                if not owner:
                    existing = [domain for domain, shop_name in domains if shop_name.casefold() == name.casefold()]
                    self._set_shop_domains(db, name, [*existing, domain])
            db.execute("UPDATE imported_orders SET shop = ?, nominal = ?, current_status = COALESCE(?, current_status) WHERE id = ?", (name, nominal.strip(), status or None, order_id))
            if remember_domain and known:
                self._fill_missing_shops(db)
            return True

    def search(self, query: str = ""):
        with self.connect() as db:
            return db.execute("SELECT id, order_date, line_name, dn_line, shop, shipping_jpy, status FROM dashboard_orders WHERE CAST(id AS TEXT) = ? OR instr(lower(line_name), lower(?)) > 0 OR instr(lower(dn_line), lower(?)) > 0 ORDER BY id DESC", (query, query, query)).fetchall()

    def products(self, order_id: int):
        with self.connect() as db:
            return db.execute("SELECT name, link, price_jpy, quantity FROM dashboard_products WHERE order_id = ? ORDER BY id", (order_id,)).fetchall()

    def has_link(self, link: str) -> bool:
        with self.connect() as db:
            if db.execute("SELECT 1 FROM dashboard_products WHERE link = ? LIMIT 1", (link,)).fetchone():
                return True
            rows = db.execute("SELECT original_json FROM imported_orders").fetchall()
        return any(link in extract_product_urls(json.loads(row[0]).get("Link", "")) for row in rows)

    def set_status(self, order_id: int, status: str):
        if status not in STATUSES:
            raise ValueError("Invalid status")
        with self.connect() as db:
            return db.execute("UPDATE dashboard_orders SET status = ? WHERE id = ?", (status, order_id)).rowcount == 1
