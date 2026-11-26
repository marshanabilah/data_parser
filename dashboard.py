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
from dashboard_parser import parse_dashboard_order
from dashboard_ui import page
from link_extractor import PublicProductExtractor, ProductFetchError, UnsafeUrlError
from order_parser import OrderParseError

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
        return page("<h1>Something lovely is on its way.</h1><p>Customer order lookup and price estimates are coming soon.</p><a href='/login'>Admin sign in</a>", title="Welcome", public=True)

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
            return page("<h1>Let's try that again.</h1><p>That password didn't match.</p><a href='/login'>Back to sign in</a>", title="Sign in", status_code=401, public=True)
        return page("<h1>Welcome back.</h1><p>Sign in to manage your jastip orders.</p><form method='post'><label>Password <input name='password' type='password' autocomplete='current-password' required></label><button>Sign in</button></form>", title="Sign in", public=True)

    async def admin(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        count = len(store.search())
        content = f"<span class='eyebrow'>Your workspace</span><h1>Good to see you.</h1><p class='intro'>A little home for all your jastip orders, from purchase to delivery.</p><div class='banner'><div><span class='eyebrow'>Ready when you are</span><h2>Let's get your orders organised.</h2><p>Paste an order collection and check the details before saving.</p></div><a class='button' href='/admin/intake'>Parse an order</a></div><div class='section-title'><div><span class='eyebrow'>At a glance</span><h2>Your order desk</h2></div><span class='hint'>{count} saved {'record' if count == 1 else 'records'}</span></div><div class='actions'><a class='action-card' href='/admin/orders'><span class='tag'>Track &amp; update</span><h2>Orders</h2><p>Find an item and follow its journey from Japan to Indonesia.</p><span class='go'>View orders →</span></a><a class='action-card' href='/admin/shops'><span class='tag'>Your preferences</span><h2>Shop settings</h2><p>Set link rules and suggested shipping fees for each shop.</p><span class='go'>Manage shops →</span></a></div><form action='/logout' method='post'><button>Sign out</button></form>"
        return page(content, title="Overview", active="overview")

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
            return page("<span class='eyebrow'>New order</span><h1>Let's add an order.</h1><p class='intro'>Paste one customer's order from one shop. We'll identify the shop and let you review it before saving.</p><form method='post'><label>LINE display name <input name='line_name' required></label><label>Order collection <textarea name='message' required></textarea></label><button>Parse and review</button></form>", title="Parse an order", active="intake")
        if not csrf(request):
            return HTMLResponse("Invalid request", status_code=403)
        try:
            form = await get_form(request)
            line_name, message = (form.get(key, [""])[0].strip() for key in ("line_name", "message"))
            key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not line_name or not message or not key:
                raise ValueError("LINE name, order text, and ANTHROPIC_API_KEY are required.")
            shop, parsed = await asyncio.to_thread(parse_dashboard_order, message, anthropic.Anthropic(api_key=key))
            settings = next((row for row in store.shops() if shop and row[0].casefold() == shop.casefold()), None)
            if settings:
                shop = settings[0]
            if len(parsed) > 20:
                raise ValueError("A submission may contain at most 20 links.")
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
            fields = f"<input type='hidden' name='token' value='{secrets.token_urlsafe(32)}'><label>LINE name <input name='line_name' value='{esc(line_name)}' required></label><label>Shop (check the detected name) <input name='shop' value='{esc(shop or '')}' maxlength='100' required></label><label>For a new shop, links per order <select name='allows_multiple'><option value='0'>One link</option><option value='1'>Multiple links</option></select></label><p>Existing shops use their saved link rule. For a new shop, the selected rule will be saved with this order.</p><label>Order date <input type='date' name='order_date' value='{datetime.now(ZoneInfo('Asia/Jakarta')).date()}' required></label>"
            fee = "" if not settings or settings[2] is None else str(settings[2])
            fields += f"<label>Shipping per record (JPY) <input name='shipping' type='number' min='0' value='{fee}'></label>"
            for item, name, yen in details:
                warning = "<p>This link already exists in an order.</p>" if store.has_link(item.link) else ""
                fields += f"<fieldset><legend>Product</legend>{warning}<label>DN Line <input name='dn' value='{esc(item.dn_line)}' required></label><label>Link <input name='link' value='{esc(item.link)}' readonly></label><label>Name <input name='name' value='{esc(name)}' required></label><label>Price (JPY) <input name='price' type='number' min='0' value='{esc(yen)}' required></label><label>Quantity <input name='quantity' type='number' min='1' value='1' required></label></fieldset>"
            return page("<span class='eyebrow'>Almost there</span><h1>Review your order.</h1><p class='intro'>Check the details before saving.</p><form method='post' action='/admin/confirm'>" + fields + "<button>Confirm and save</button></form><a href='/admin/intake'>Cancel</a>", title="Review order", active="intake")
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
            setting = next((row for row in store.shops() if row[0].casefold() == shop.casefold()), None)
            rule = one("allows_multiple") if not setting else str(setting[1])
            if rule not in ("0", "1") or not shop or len(shop) > 100:
                raise ValueError("Invalid shop")
            if setting:
                shop = setting[0]
            multiple = rule == "1"
            names, links, dns, prices, quantities = (form[key] for key in ("name", "link", "dn", "price", "quantity"))
            if not names or len(names) > 20 or len({len(names), len(links), len(dns), len(prices), len(quantities)}) != 1:
                raise ValueError("Invalid product fields")
            products = [ProductLine(name, link, int(price), int(qty)) for name, link, price, qty in zip(names, links, prices, quantities)]
            if multiple and len(set(dns)) != 1:
                raise ValueError("Orders must have one DN Line")
            shipping = int(one("shipping")) if one("shipping") else None
            groups = [(dns[0], tuple(products))] if multiple else [(dn, (product,)) for dn, product in zip(dns, products)]
            records = [OrderRecord(date.fromisoformat(one("order_date")), one("line_name"), dn, shop, multiple, shipping, lines) for dn, lines in groups]
            ids = store.save(one("token"), records, (shop, multiple) if not setting else None)
            return page("<span class='eyebrow'>All done</span><h1>Order saved.</h1><p>Item IDs: " + ", ".join(map(str, ids)) + "</p><a class='button' href='/admin/orders'>View orders</a>", title="Order saved", active="orders")
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
        empty = "<div class='empty-state'>No matching orders yet. Try another search or add an order.</div>" if not result else ""
        return page("<span class='eyebrow'>Track your items</span><h1>Orders</h1><p class='intro'>Find an order and update its journey.</p><form><label>Search by item ID, LINE name, or DN Line <input name='q' value='" + html.escape(query, quote=True) + "'></label><button>Search</button></form>" + "".join(result) + empty, title="Orders", active="orders")

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
        for index, (name, multiple, fee) in enumerate(store.shops()):
            safe_name = html.escape(name, quote=True)
            selected_single = " selected" if not multiple else ""
            selected_multi = " selected" if multiple else ""
            value = "" if fee is None else str(fee)
            rows.append(f"<tr><td data-label='Shop'><strong>{safe_name}</strong><form id='shop-{index}' method='post'></form><input form='shop-{index}' type='hidden' name='name' value='{safe_name}'></td><td data-label='Links per order'><select form='shop-{index}' name='allows_multiple' aria-label='Links per order for {safe_name}'><option value='0'{selected_single}>One link</option><option value='1'{selected_multi}>Multiple links</option></select></td><td data-label='Suggested shipping (JPY)'><input form='shop-{index}' type='number' min='0' name='shipping_jpy' value='{value}' aria-label='Suggested shipping in JPY for {safe_name}' placeholder='Not set'></td><td><button form='shop-{index}' class='save-row'>Save<span class='sr-only'> {safe_name}</span></button></td></tr>")
        return page("<span class='eyebrow'>Your preferences</span><h1>Shop settings</h1><p class='intro'>Choose how many links each shop allows and set an optional suggested shipping fee.</p><div class='shop-table-wrap'><table class='shop-table'><thead><tr><th>Shop</th><th>Links per order</th><th>Suggested shipping (JPY)</th><th><span class='sr-only'>Actions</span></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div><h2 class='add-shop-heading'>Add a shop</h2><form method='post' class='add-shop-form'><label>Shop <input name='name' required></label> <label>Links <select name='allows_multiple'><option value='0'>One link</option><option value='1'>Multiple links</option></select></label> <label>Suggested shipping (JPY) <input type='number' min='0' name='shipping_jpy'></label> <button>Add shop</button></form>", title="Shop settings", active="shops")

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
