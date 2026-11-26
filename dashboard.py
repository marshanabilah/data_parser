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
from urllib.parse import parse_qs, urlencode
from zoneinfo import ZoneInfo

import httpx
from csv_orders import parse_csv_orders

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from dashboard_store import DashboardStore, valid_jpy
from dashboard_orders import OrderRecord, ProductLine, STATUSES
from dashboard_parser import parse_dashboard_order
from dashboard_ui import page as render_page
from link_extractor import PublicProductExtractor, ProductFetchError, UnsafeUrlError
from order_parser import OrderParseError
from pricing import calculate

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

    def page(content, **kwargs):
        return render_page(content, rate=store.exchange_rate(), **kwargs)

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
        count = len(store.search()) + len(store.imported())
        content = f"<span class='eyebrow'>Your workspace</span><h1>Good to see you.</h1><p class='intro'>A little home for all your jastip orders, from purchase to delivery.</p><div class='banner'><div><span class='eyebrow'>Ready when you are</span><h2>Let's get your orders organised.</h2><p>Paste an order collection and check the details before saving.</p></div><a class='button' href='/admin/intake'>Parse an order</a></div><div class='section-title'><div><span class='eyebrow'>At a glance</span><h2>Your order desk</h2></div><span class='hint'>{count} saved {'record' if count == 1 else 'records'}</span></div><div class='actions'><a class='action-card' href='/admin/orders'><span class='tag'>Track &amp; update</span><h2>Orders</h2><p>Find an item and follow its journey from Japan to Indonesia.</p><span class='go'>View orders →</span></a><a class='action-card' href='/admin/shops'><span class='tag'>Your preferences</span><h2>Shop settings</h2><p>Set link rules and suggested shipping fees for each shop.</p><span class='go'>Manage shops →</span></a></div><form action='/logout' method='post'><button>Sign out</button></form>"
        return page(content, title="Overview", active="overview")

    async def get_form(request: Request, max_bytes=65536):
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            raise ValueError("Invalid form")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > max_bytes:
                raise ValueError("Form too large. Split the CSV into smaller files and try again.")
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
            key = os.environ.get("OPENROUTER_API_KEY", "").strip()
            if not line_name or not message or not key:
                raise ValueError("LINE name, order text, and OPENROUTER_API_KEY are required.")
            shop, parsed = await asyncio.to_thread(parse_dashboard_order, message, key, display_name=line_name)
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
                fields += f"<fieldset><legend>Product</legend>{warning}<label>Link <input name='link' value='{esc(item.link)}' readonly></label><label>Name <input name='name' value='{esc(name)}' required></label><label>Price (JPY) <input name='price' type='number' min='0' value='{esc(yen)}' required></label><label>Quantity <input name='quantity' type='number' min='1' value='1' required></label></fieldset>"
            return page("<span class='eyebrow'>Almost there</span><h1>Review your order.</h1><p class='intro'>Check the details before saving.</p><form method='post' action='/admin/confirm'>" + fields + "<button>Confirm and save</button></form><a href='/admin/intake'>Cancel</a>", title="Review order", active="intake")
        except (ValueError, OrderParseError) as exc:
            return page("<h1>Could not parse this order.</h1><p>" + html.escape(str(exc)) + "</p><a href='/admin/intake'>Try again</a>", title="Parsing problem", active="intake", status_code=400)

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
            names, links, prices, quantities = (form[key] for key in ("name", "link", "price", "quantity"))
            dns = [one("line_name")] * len(names)
            if not names or len(names) > 20 or len({len(names), len(links), len(prices), len(quantities)}) != 1:
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

    async def calculator(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        error = ""
        result = ""
        price = ""
        quantity = "1"
        if request.method == "POST":
            if not csrf(request):
                return HTMLResponse("Invalid request", status_code=403)
            try:
                form = await get_form(request)
                action = form.get("action", [""])[0]
                if action != "calculate":
                    raise ValueError("Invalid request.")
                price = form.get("price", [""])[0].strip()
                quantity = form.get("quantity", [""])[0].strip()
                rate = store.exchange_rate()
                if rate is None:
                    raise ValueError("Set the exchange rate before calculating.")
                items, fee, total = calculate(price, quantity, rate)
                def rupiah(n):
                    return f"Rp{n:,}".replace(",", ".")
                result = f"<section aria-live='polite'><h2>Estimated total: {rupiah(total)}</h2><p>Item cost: {rupiah(items)} · Order fee: {rupiah(fee)} · Rate: Rp{html.escape(rate)} per ¥1</p><p>Shipping is not included.</p></section>"
            except ValueError as exc:
                error = f"<p role='alert'>{html.escape(str(exc))}</p>"
        content = ("<span class='eyebrow'>Pricing</span><h1>Jastip calculator</h1>"
                   "<p class='intro'>Estimate an order in rupiah using your current JPY exchange rate. Shipping is not included.</p>"
                   + error + "<h2>Estimate an item</h2><form method='post'><input type='hidden' name='action' value='calculate'>"
                   + f"<label>Item price (JPY) <input name='price' type='number' min='0' step='1' value='{html.escape(price, quote=True)}' required></label>"
                   + f"<label>Quantity <input name='quantity' type='number' min='1' max='10000' step='1' value='{html.escape(quantity, quote=True)}' required></label>"
                   + "<p>Order fee: Rp15.000 per item</p><button>Calculate estimate</button></form>" + result)
        return page(content, title="Jastip calculator", active="calculator")

    async def exchange_rate(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        error = ""
        if request.method == "POST":
            if not csrf(request):
                return HTMLResponse("Invalid request", status_code=403)
            try:
                form = await get_form(request)
                store.set_exchange_rate(form.get("rate", [""])[0])
                return RedirectResponse("/admin/rate", status_code=303)
            except ValueError as exc:
                error = f"<p role='alert'>{html.escape(str(exc))}</p>"
        rate = store.exchange_rate()
        current = f"Current saved rate: Rp{html.escape(rate)} per ¥1." if rate is not None else "No rate has been saved yet."
        content = ("<span class='eyebrow'>Pricing settings</span><h1>Exchange rate</h1>"
                   f"<p class='intro'>{current} Update this whenever your rate changes.</p>" + error
                   + "<form method='post'><label>IDR per ¥1 <input name='rate' type='number' min='0.0001' step='0.0001' "
                   + f"value='{html.escape(rate or '', quote=True)}' required></label><button>Save rate</button></form>"
                   + "<p><a href='/admin/calculator'>Go to calculator</a></p>")
        return page(content, title="Exchange rate")

    async def import_csv(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        if request.method == "GET":
            return page("<span class='eyebrow'>From Google Sheets</span><h1>Import orders</h1><p class='intro'>Choose an exported CSV or paste its contents. You'll review the rows before saving. Imports stay on this server and do not use AI.</p><form method='post'><label>CSV file <input type='file' accept='.csv,text/csv' id='csv-file'></label><label>CSV contents <textarea name='csv' id='csv-content' required></textarea></label><button>Review CSV</button></form><script>document.getElementById('csv-file').addEventListener('change', async function () { if (this.files[0]) document.getElementById('csv-content').value = await this.files[0].text(); });</script>", title="Import CSV", active="import")
        if not csrf(request):
            return HTMLResponse("Invalid request", status_code=403)
        try:
            form = await get_form(request, max_bytes=4 * 1024 * 1024)
            source = form.get("csv", [""])[0]
            records = parse_csv_orders(source)
            if form.get("confirm", [""])[0] == "yes":
                ids = store.import_orders(records)
                renumbered = sum(order_id != int(row["Order No."]) for order_id, row in zip(ids, records))
                return page(f"<h1>Import complete.</h1><p>{len(records)} orders saved; {renumbered} received a new item ID. Original order numbers remain in each record. Imported orders need review because their shop is not in the CSV.</p><a class='button' href='/admin/orders'>View orders</a>", title="Import complete", active="orders")
            def esc(value):
                return html.escape(str(value), quote=True)
            rows = "".join(f"<tr><td>{esc(r['Order No.'])}</td><td>{esc(r['DN Line'])}</td><td>{esc(r['Items'])}</td><td>{esc(r['Nominal']) if r['Nominal'].strip() else 'Needs review'}</td><td>{esc(r['Status'])}</td></tr>" for r in records)
            body = "<h1>Review CSV</h1><p>Each row becomes a separate order. Repeated or existing order numbers receive new item IDs; the original numbers stay available for reference. Dates are kept exactly as provided without adding a year. All rows will need review because the shop is missing; blank prices also need review.</p><div class='shop-table-wrap'><table class='shop-table'><thead><tr><th>Original order number</th><th>LINE name</th><th>Item</th><th>Price (JPY)</th><th>Original status</th></tr></thead><tbody>" + rows + "</tbody></table></div><form method='post'><input type='hidden' name='confirm' value='yes'><input type='hidden' name='csv' value='" + esc(source) + "'><button>Confirm import</button></form><a href='/admin/import'>Cancel</a>"
            return page(body, title="Review CSV", active="import")
        except ValueError as exc:
            return page("<h1>Could not import CSV</h1><p>" + html.escape(str(exc)) + "</p><a href='/admin/import'>Try again</a>", title="Import problem", active="import", status_code=400)

    async def orders(request: Request):
        if not valid(request):
            return RedirectResponse("/login", status_code=303)
        if request.method == "POST":
            if not csrf(request):
                return HTMLResponse("Invalid request", status_code=403)
            try:
                form = await get_form(request)
                if form.get("kind", [""])[0] == "imported":
                    updated = store.update_imported(int(form["id"][0]), form["shop"][0], form["nominal"][0])
                else:
                    updated = store.set_status(int(form["id"][0]), form["status"][0])
                if not updated:
                    raise ValueError("Unknown order")
            except (ValueError, KeyError):
                return HTMLResponse("Invalid status update", status_code=400)
            params = {}
            if form.get("view", [""])[0] == "review":
                params["view"] = "review"
            if form.get("q", [""])[0].strip():
                params["q"] = form["q"][0][:200]
            return RedirectResponse("/admin/orders" + ("?" + urlencode(params) if params else ""), status_code=303)
        query = request.query_params.get("q", "")[:200]
        review_only = request.query_params.get("view") == "review"
        imported = store.imported()
        review_count = sum(not shop.strip() or not valid_jpy(nominal) for _, _, shop, nominal in imported)
        result = []
        def esc(value):
            return html.escape(str(value), quote=True)
        view_fields = f"<input type='hidden' name='view' value='{'review' if review_only else 'all'}'><input type='hidden' name='q' value='{esc(query)}'>"
        if not review_only:
            for order_id, order_date, name, dn, shop, shipping, status in store.search(query):
                products = store.products(order_id)
                lines = "".join(f"<li>{esc(title)} — ¥{price} × {quantity} <a href='{esc(link)}' rel='noreferrer'>Product</a></li>" for title, link, price, quantity in products)
                summary = products[0][0] + (f" + {len(products) - 1} more" if len(products) > 1 else "") if products else "No product"
                options = "".join(f"<option{' selected' if state == status else ''}>{esc(state)}</option>" for state in STATUSES)
                details = f"<details><summary>Details &amp; update</summary><div class='order-details'><p>Shop: {esc(shop)} · Shipping: {esc(shipping if shipping is not None else 'not set')} JPY</p><ul>{lines}</ul><form method='post'>{view_fields}<input type='hidden' name='id' value='{order_id}'><label>Status <select name='status'>{options}</select></label><button>Update status</button></form></div></details>"
                result.append((order_id, f"<tr><td>#{order_id}</td><td>{esc(order_date)}</td><td>{esc(name)}</td><td>{esc(summary)}</td><td>{esc(status)}</td><td>{details}</td></tr>"))
        for order_id, row, shop, nominal in store.imported(query):
            needs_review = not shop.strip() or not valid_jpy(nominal)
            if review_only and not needs_review:
                continue
            details = "".join(f"<li><strong>{esc(key)}:</strong> {esc(row[key]) or '—'}</li>" for key in ("Order No.", "Date", "DN Line", "Items", "Link", "Nominal", "Arrived Date", "Status", "Box"))
            details += "".join(f"<li><strong>{esc(column['header'] or 'Extra column')}:</strong> {esc(column['value'])}</li>" for column in row.get("Extra columns", []) if column["value"])
            form = f"<details><summary>Details &amp; review</summary><div class='order-details'><p>Original sheet values</p><ul>{details}</ul><form method='post'>{view_fields}<input type='hidden' name='kind' value='imported'><input type='hidden' name='id' value='{order_id}'><label>Shop <input name='shop' value='{esc(shop)}'></label><label>Price (JPY) <input name='nominal' value='{esc(nominal)}'></label><button>Save review</button></form></div></details>"
            review_text = "Needs review" if needs_review else "Reviewed"
            result.append((order_id, f"<tr><td>#{order_id}</td><td>{esc(row['Date']) or '—'}</td><td>{esc(row['DN Line'])}</td><td>{esc(row['Items']) or '—'}</td><td><span class='review-label'>{review_text}</span><br>{esc(row['Status'])}</td><td>{form}</td></tr>"))
        result.sort(key=lambda record: record[0], reverse=True)
        empty = "<div class='empty-state'>No matching orders yet. Try another search or add an order.</div>" if not result else ""
        search_args = "?" + urlencode({"q": query}) if query else ""
        review_args = "?" + urlencode({"view": "review", **({"q": query} if query else {})})
        tabs = f"<nav class='order-tabs' aria-label='Order views'><a href='/admin/orders{esc(search_args)}'{' aria-current=page' if not review_only else ''}>All orders</a><a href='/admin/orders{esc(review_args)}'{' aria-current=page' if review_only else ''}>Needs review <span class='review-count'>{review_count}</span></a></nav>"
        search_form = f"<form class='order-search' method='get'>{'<input type=hidden name=view value=review>' if review_only else ''}<label>Search by item ID or LINE display name <input name='q' value='{esc(query)}'></label><button>Search</button></form>"
        table = "<div class='orders-table-wrap'><table class='orders-table'><thead><tr><th>Item ID</th><th>Date</th><th>LINE name</th><th>Item</th><th>Status</th><th>Details</th></tr></thead><tbody>" + "".join(row for _, row in result) + "</tbody></table></div>" if result else ""
        return page("<span class='eyebrow'>Track your items</span><h1>Orders</h1><p class='intro'>Find an order and update its journey.</p>" + tabs + search_form + table + empty, title="Orders", active="orders")

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
        return page("<span class='eyebrow'>Your preferences</span><h1>Shop settings</h1><p class='intro'>Choose how many links each shop allows and set an optional suggested shipping fee.</p><h2>Add a shop</h2><form method='post' class='add-shop-form'><label>Shop <input name='name' required></label> <label>Links <select name='allows_multiple'><option value='0'>One link</option><option value='1'>Multiple links</option></select></label> <label>Suggested shipping (JPY) <input type='number' min='0' name='shipping_jpy'></label> <button>Add shop</button></form><h2 class='add-shop-heading'>Saved shops</h2><div class='shop-table-wrap'><table class='shop-table'><thead><tr><th>Shop</th><th>Links per order</th><th>Suggested shipping (JPY)</th><th><span class='sr-only'>Actions</span></th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>", title="Shop settings", active="shops")

    async def logout(request: Request):
        if not csrf(request):
            return HTMLResponse("Invalid request", status_code=403)
        token = request.cookies.get("admin_session", "")
        with sqlite3.connect(db_path) as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (token_hash(token),))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("admin_session", secure=secure, httponly=True, samesite="strict")
        return response

    return Starlette(routes=[Route("/", public), Route("/login", login, methods=["GET", "POST"]), Route("/admin", admin), Route("/admin/shops", shops, methods=["GET", "POST"]), Route("/admin/calculator", calculator, methods=["GET", "POST"]), Route("/admin/rate", exchange_rate, methods=["GET", "POST"]), Route("/admin/intake", intake, methods=["GET", "POST"]), Route("/admin/import", import_csv, methods=["GET", "POST"]), Route("/admin/confirm", confirm, methods=["POST"]), Route("/admin/orders", orders, methods=["GET", "POST"]), Route("/logout", logout, methods=["POST"])])


def app_factory() -> Starlette:
    return create_app(os.environ.get("DASHBOARD_DB", "dashboard.sqlite3"), os.environ.get("ADMIN_PASSWORD_HASH", ""), os.environ.get("DASHBOARD_SECRET", ""), secure=os.environ.get("DASHBOARD_INSECURE_LOCAL") != "1")
