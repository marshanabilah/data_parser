"""Single-admin dashboard access."""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

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
        return HTMLResponse("<h1>Admin dashboard</h1><form action='/logout' method='post'><button>Sign out</button></form>")

    async def logout(request: Request):
        if not csrf(request):
            return HTMLResponse("Invalid request", status_code=403)
        token = request.cookies.get("admin_session", "")
        with sqlite3.connect(db_path) as db:
            db.execute("DELETE FROM sessions WHERE id = ?", (token_hash(token),))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie("admin_session", secure=secure, httponly=True, samesite="strict")
        return response

    return Starlette(routes=[Route("/", public), Route("/login", login, methods=["GET", "POST"]), Route("/admin", admin), Route("/logout", logout, methods=["POST"])])


def app_factory() -> Starlette:
    return create_app(os.environ.get("DASHBOARD_DB", "dashboard.sqlite3"), os.environ.get("ADMIN_PASSWORD_HASH", ""), os.environ.get("DASHBOARD_SECRET", ""), secure=os.environ.get("DASHBOARD_INSECURE_LOCAL") != "1")
