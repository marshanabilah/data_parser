"""Single-admin dashboard access."""

import hashlib
import hmac
import html
import os
import secrets
import sqlite3
import time
import asyncio
from datetime import datetime
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import anthropic
import httpx

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from dashboard_store import DashboardStore
from dashboard_orders import OrderRecord, ProductLine, STATUSES
from link_extractor import PublicProductExtractor, ProductFetchError, UnsafeUrlError
from order_parser import ClaudeOrderMessageInterpreter, OrderParseError, parse_order_message

SESSION_SECONDS = 8 * 60 * 60


def password_hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()


def create_app(db_path: str, credential: str, secret: str, secure: bool = True) -> Starlette:
    if not credential or len(secret) < 32:
        raise ValueError("Admin password hash and a session secret of at least 32 characters are required")
    try:
        salt, digest = credential.split(":")
        if len(bytes.fromhex(salt)) < 16 or len(bytes.fromhex(digest)) != 32:
            raise ValueError
    except ValueError as exc:
        raise ValueError("Invalid admin password hash") from exc
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, expires REAL, credential TEXT)")
    store = DashboardStore(db_path)

    def token_hash(token: str) -> str:
        return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()

    def valid(request: Request) -> bool:
        token = request.cookies.get("admin_session", "")
        if not token or len(token) > 200:
            return False
        with sqlite3.connect(db_path) as db:
            row = db.execute("SELECT expires, credential FROM sessions WHERE id = ?", (token_hash(token),)).fetchone()
        return bool(row and row[0] > time.time() and hmac.compare_digest(row[1], token_hash(credential)))

    def csrf(request: Request) -> bool:
        origin = request.headers.get("origin")
        return bool(origin and origin == str(request.base_url).rstrip("/"))

    async def public(request: Request):
        return HTMLResponse("<h1>Jastip</h1><p>Customer tools are coming soon.</p>")

    async def login(request: Request):
        if request.method == "POST":
            if not csrf(request):
                return HTMLResponse("Invalid request", status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
                return HTMLResponse("Invalid request", status_code=400)
            body = await request.body()
            if len(body) > 4096:
                return HTMLResponse("Invalid request", status_code=400)
            password = parse_qs(body.decode("utf-8", errors="replace")).get("password", [""])[0]
            if len(password) <= 1024 and hmac.compare_digest(password_hash(password, salt), digest):
                token = secrets.token_urlsafe(32)
                with sqlite3.connect(db_path) as db:
                    db.execute("DELETE FROM sessions WHERE expires <= ? OR credential != ?", (time.time(), token_hash(credential)))
                    db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (token_hash(token), time.time() + SESSION_SECONDS, token_hash(credential)))
                response = RedirectResponse("/admin", status_code=303)
                response.set_cookie("admin_session", token, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite="strict")
                return response
            return HTMLResponse("<p>Incorrect password.</p><a href='/login'>Try again</a>", status_code=401)
        return HTMLResponse("<h1>Admin sign in</h1><form method='post'><label>Password <input name='password' type='password' autocomplete='current-password' required></label><button>Sign in</button></form>")

    async def admin(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse("<h1>Admin dashboard</h1><p><a href='/admin/shops'>Shop settings</a> · <a href='/admin/intake'>Parse an order</a> · <a href='/admin/orders'>Orders</a></p><form action='/logout' method='post'><button>Sign out</button></form>")

    async def get_form(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            raise ValueError("Invalid form")
        body = await request.body()
        if len(body) > 65536:
            raise ValueError("Form too large")
        return parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)

    async def intake(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        if request.method == "GET":
            options = "".join(f"<option>{html.escape(name)}</option>" for name, _, _ in store.shops())
            return HTMLResponse("<h1>Parse an order</h1><p>Paste one customer's order from one shop. For multi-link shops, links in this submission will share one record. Submit separate orders separately.</p><form method='post'><label>LINE display name <input name='line_name' required></label><label>Shop <select name='shop'>" + options + "</select></label><label>Order collection <textarea name='message' required></textarea></label><button>Parse and review</button></form><p><a href='/admin'>Back</a></p>")
        if not csrf(request):
            return HTMLResponse("Invalid request", status_code=403)
        try:
            form = await get_form(request)
            line_name, shop, message = (form.get(key, [""])[0].strip() for key in ("line_name", "shop", "message"))
            settings = next((row for row in store.shops() if row[0] == shop), None)
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not line_name or not settings or not message or not key:
                raise ValueError("LINE name, shop, order text, and ANTHROPIC_API_KEY are required.")
            parsed = await asyncio.to_thread(parse_order_message, message, ClaudeOrderMessageInterpreter(anthropic.Anthropic(api_key=key)))
            if len(parsed) > 20:
                raise ValueError("A submission may contain at most 20 links.")
            if settings[1] and len({item.dn_line for item in parsed}) != 1:
                raise ValueError("Multi-link orders must have one DN Line; submit different orders separately.")
            async with httpx.AsyncClient() as client:
                extractor = PublicProductExtractor(client)
                details = []
                for item in parsed:
                    try:
                        result = await extractor.extract(item.link)
                        name, price = item.items_override or result.items or "", item.nominal_override or result.nominal or ""
                    except UnsafeUrlError:
                        raise ValueError("A link is not a safe public HTTPS page")
                    except ProductFetchError:
                        name, price = item.items_override or "", item.nominal_override or ""
                    # Only unambiguous whole-yen amounts are suggested for confirmation.
                    import re
                    match = re.fullmatch(r"\s*(?:¥|￥|JPY\s*)?([0-9,]+)(?:\s*円)?\s*", price, re.IGNORECASE)
                    yen = match.group(1).replace(",", "") if match else ""
                    details.append((item, name, yen))
            def esc(value):
                return html.escape(str(value), quote=True)
            fields = f"<input type='hidden' name='token' value='{secrets.token_urlsafe(32)}'><input type='hidden' name='shop' value='{esc(shop)}'><label>LINE name <input name='line_name' value='{esc(line_name)}' required></label><label>Order date <input type='date' name='order_date' value='{datetime.now(ZoneInfo('Asia/Jakarta')).date()}' required></label>"
            fee = "" if settings[2] is None else str(settings[2])
            fields += f"<label>Shipping per record (JPY) <input name='shipping' type='number' min='0' value='{fee}'></label>"
            for item, name, yen in details:
                warning = "<p>This link already exists in an order.</p>" if store.has_link(item.link) else ""
                fields += f"<fieldset><legend>Product</legend>{warning}<label>DN Line <input name='dn' value='{esc(item.dn_line)}' required></label><label>Link <input name='link' value='{esc(item.link)}' readonly></label><label>Name <input name='name' value='{esc(name)}' required></label><label>Price (JPY) <input name='price' type='number' min='0' value='{esc(yen)}' required></label><label>Quantity <input name='quantity' type='number' min='1' value='1' required></label></fieldset>"
            return HTMLResponse("<h1>Review order</h1><p>Check all fields before saving.</p><form method='post' action='/admin/confirm'>" + fields + "<button>Confirm and save</button></form><a href='/admin/intake'>Cancel</a>")
        except (ValueError, OrderParseError, anthropic.APIError):
            return HTMLResponse("Could not parse the order. Check the order details and API configuration, then try again.", status_code=400)

    async def confirm(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        if not csrf(request):
            return HTMLResponse("Invalid request", status_code=403)
        try:
            from datetime import date
            form = await get_form(request)
            def one(key):
                return form[key][0].strip()
            shop = one("shop")
            setting = next(row for row in store.shops() if row[0] == shop)
            names, links, dns, prices, quantities = (form[key] for key in ("name", "link", "dn", "price", "quantity"))
            if not names or len(names) > 20 or len({len(names), len(links), len(dns), len(prices), len(quantities)}) != 1:
                raise ValueError("Invalid product fields")
            products = [ProductLine(name, link, int(price), int(qty)) for name, link, price, qty in zip(names, links, prices, quantities)]
            if setting[1] and len(set(dns)) != 1:
                raise ValueError("Orders must have one DN Line")
            shipping = int(one("shipping")) if one("shipping") else None
            groups = [(dns[0], tuple(products))] if setting[1] else [(dn, (product,)) for dn, product in zip(dns, products)]
            records = [OrderRecord(date.fromisoformat(one("order_date")), one("line_name"), dn, shop, bool(setting[1]), shipping, lines) for dn, lines in groups]
            ids = store.save(one("token"), records)
            return HTMLResponse("<h1>Saved</h1><p>Item IDs: " + ", ".join(map(str, ids)) + "</p><a href='/admin/orders'>View orders</a>")
        except (ValueError, KeyError, StopIteration, OverflowError):
            return HTMLResponse("Invalid order. Return to the preview and check all fields.", status_code=400)

    async def orders(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        if request.method == "POST":
            if not csrf(request):
                return HTMLResponse("Invalid request", status_code=403)
            try:
                form = await get_form(request)
                if not store.set_status(int(form["id"][0]), form["status"][0]):
                    raise ValueError("Unknown order")
            except (ValueError, KeyError):
                return HTMLResponse("Invalid status update", status_code=400)
            return RedirectResponse("/admin/orders", status_code=303)
        query = request.query_params.get("q", "")[:200]
        result = []
        for order_id, order_date, name, dn, shop, shipping, status in store.search(query):
            def escape(value):
                return html.escape(str(value))
            lines = "".join(f"<li>{escape(title)} — ¥{price} × {quantity} <a href='{html.escape(link, quote=True)}' rel='noreferrer'>Product</a></li>" for title, link, price, quantity in store.products(order_id))
            options = "".join(f"<option{' selected' if state == status else ''}>{escape(state)}</option>" for state in STATUSES)
            result.append(f"<section><h2>Item {order_id} — {escape(shop)}</h2><p>{escape(order_date)} · {escape(name)} · {escape(dn)} · Shipping: {escape(shipping if shipping is not None else 'not set')} JPY</p><ul>{lines}</ul><form method='post'><input type='hidden' name='id' value='{order_id}'><label>Status <select name='status'>{options}</select></label><button>Update</button></form></section>")
        return HTMLResponse("<h1>Orders</h1><form><label>Search <input name='q' value='" + html.escape(query, quote=True) + "'></label><button>Search</button></form>" + "".join(result) + "<p><a href='/admin'>Back</a></p>")

    async def shops(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        if request.method == "POST":
            if not csrf(request):
                return HTMLResponse("Invalid request", status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
                return HTMLResponse("Invalid request", status_code=400)
            body = await request.body()
            if len(body) > 4096:
                return HTMLResponse("Invalid request", status_code=400)
            fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            name = fields.get("name", [""])[0].strip()
            fee = fields.get("shipping_jpy", [""])[0].strip()
            rule = fields.get("allows_multiple", [""])[0]
            if len(name) > 100 or rule not in ("0", "1") or (fee and (not fee.isascii() or not fee.isdigit() or len(fee) > 9)):
                return HTMLResponse("Invalid shop settings", status_code=400)
            try:
                store.set_shop(name, rule == "1", int(fee) if fee else None)
            except ValueError:
                return HTMLResponse("Invalid shop settings", status_code=400)
            return RedirectResponse("/admin/shops", status_code=303)
        rows = []
        for name, multiple, fee in store.shops():
            safe_name = html.escape(name, quote=True)
            selected_single = " selected" if not multiple else ""
            selected_multi = " selected" if multiple else ""
            value = "" if fee is None else str(fee)
            rows.append(f"<form method='post'><label>Shop <input name='name' value='{safe_name}' required></label> <label>Links <select name='allows_multiple'><option value='0'{selected_single}>One</option><option value='1'{selected_multi}>Multiple</option></select></label> <label>Suggested shipping (JPY) <input type='number' min='0' name='shipping_jpy' value='{value}'></label> <button>Save</button></form>")
        return HTMLResponse("<h1>Shop settings</h1><p><a href='/admin'>Back to dashboard</a></p>" + "".join(rows) + "<h2>Add shop</h2><form method='post'><label>Shop <input name='name' required></label> <label>Links <select name='allows_multiple'><option value='0'>One</option><option value='1'>Multiple</option></select></label> <label>Suggested shipping (JPY) <input type='number' min='0' name='shipping_jpy'></label> <button>Add shop</button></form>")

    async def logout(request: Request):
        if not csrf(request):
            return HTMLResponse("Invalid request", status_code=403)
        token = request.cookies.get("admin_session", "")
        with sqlite3.connect(db_path) as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (token_hash(token),))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("admin_session", secure=secure, httponly=True, samesite="strict")
        return response

    return Starlette(routes=[Route("/", public), Route("/login", login, methods=["GET", "POST"]), Route("/admin", admin), Route("/admin/shops", shops, methods=["GET", "POST"]), Route("/admin/intake", intake, methods=["GET", "POST"]), Route("/admin/confirm", confirm, methods=["POST"]), Route("/admin/orders", orders, methods=["GET", "POST"]), Route("/logout", logout, methods=["POST"])])


def app_factory() -> Starlette:
    return create_app(os.environ.get("DASHBOARD_DB", "dashboard.sqlite3"), os.environ.get("ADMIN_PASSWORD_HASH", ""), os.environ.get("DASHBOARD_SECRET", ""), secure=os.environ.get("DASHBOARD_INSECURE_LOCAL") != "1")
