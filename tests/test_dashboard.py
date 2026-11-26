import sqlite3
import time

import pytest
from starlette.testclient import TestClient

from dashboard import create_app, password_hash


@pytest.fixture
def client(tmp_path):
    salt = "01" * 16
    credential = f"{salt}:{password_hash('correct horse', salt)}"
    path = str(tmp_path / "sessions.db")
    return TestClient(create_app(path, credential, "s" * 32, secure=False)), path, credential


def test_login_logout_and_public_access(client):
    browser, _, _ = client
    assert browser.get("/").status_code == 200
    assert browser.get("/admin", follow_redirects=False).status_code == 303
    assert browser.post("/login", data={"password": "wrong"}, headers={"origin": "http://testserver"}).status_code == 401
    assert browser.post("/login", data={"password": "correct horse"}, headers={"origin": "http://testserver"}, follow_redirects=False).status_code == 303
    assert browser.get("/admin").status_code == 200
    old_cookie = browser.cookies.get("admin_session")
    assert browser.post("/logout", headers={"origin": "http://testserver"}).status_code == 200
    browser.cookies.set("admin_session", old_cookie)
    assert browser.get("/admin", follow_redirects=False).status_code == 303


def test_expiry_and_password_change_revoke_access(client):
    browser, path, _ = client
    browser.post("/login", data={"password": "correct horse"}, headers={"origin": "http://testserver"})
    with sqlite3.connect(path) as db:
        db.execute("UPDATE sessions SET expires = ?", (time.time() - 1,))
    assert browser.get("/admin", follow_redirects=False).status_code == 303
    browser.post("/login", data={"password": "correct horse"}, headers={"origin": "http://testserver"})
    salt = "02" * 16
    changed = TestClient(create_app(path, f"{salt}:{password_hash('new password', salt)}", "s" * 32, secure=False))
    changed.cookies.set("admin_session", browser.cookies.get("admin_session"))
    assert changed.get("/admin", follow_redirects=False).status_code == 303


def test_rejects_cross_origin_and_missing_configuration(client, tmp_path):
    browser, _, _ = client
    assert browser.post("/login", data={"password": "correct horse"}, headers={"origin": "https://evil.example"}).status_code == 403
    assert browser.post("/logout").status_code == 403
    with pytest.raises(ValueError):
        create_app(str(tmp_path / "x"), "", "")


def test_shop_settings_require_admin_and_persist(client):
    browser, _, _ = client
    assert browser.get("/admin/shops", follow_redirects=False).status_code == 303
    browser.post("/login", data={"password": "correct horse"}, headers={"origin": "http://testserver"})
    assert "Mercari" in browser.get("/admin/shops").text
    assert browser.post("/admin/shops", data={"name": "UMS", "allows_multiple": "1", "shipping_jpy": "450"}, headers={"origin": "http://testserver"}, follow_redirects=False).status_code == 303
    assert "value='450'" in browser.get("/admin/shops").text
    assert browser.post("/admin/shops", data={"name": "UMS", "allows_multiple": "1", "shipping_jpy": "50"}, headers={"origin": "https://evil.example"}).status_code == 403


def test_confirm_search_and_status(client):
    browser, _, _ = client
    payload = {"token": "test-token", "shop": "UMS", "line_name": "Bela", "order_date": "2026-11-26", "shipping": "", "dn": ["DN1", "DN1"], "link": ["https://example.com/a", "https://example.com/b"], "name": ["A", "B"], "price": ["100", "200"], "quantity": ["1", "2"]}
    assert browser.post("/admin/confirm", data=payload, headers={"origin": "http://testserver"}, follow_redirects=False).status_code == 303
    browser.post("/login", data={"password": "correct horse"}, headers={"origin": "http://testserver"})
    saved = browser.post("/admin/confirm", data=payload, headers={"origin": "http://testserver"})
    assert "Item IDs: 1" in saved.text
    assert "Item IDs: 1" in browser.post("/admin/confirm", data=payload, headers={"origin": "http://testserver"}).text
    assert "Bela" in browser.get("/admin/orders?q=DN1").text
    browser.post("/admin/orders", data={"id": "1", "status": "Completed"}, headers={"origin": "http://testserver"})
    assert "selected>Completed" in browser.get("/admin/orders").text


def test_intake_parses_preview_and_requires_price(client, monkeypatch):
    from models import ExtractedProduct, SubmittedLink
    import dashboard

    browser, _, _ = client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(dashboard, "parse_order_message", lambda *args: (SubmittedLink(dn_line="DN1", link="https://example.com/a"),))

    async def extract(_self, _link):
        return ExtractedProduct(items="Album", nominal=None)

    monkeypatch.setattr(dashboard.PublicProductExtractor, "extract", extract)
    browser.post("/login", data={"password": "correct horse"}, headers={"origin": "http://testserver"})
    preview = browser.post("/admin/intake", data={"line_name": "Bela", "shop": "UMS", "message": "DN1 https://example.com/a"}, headers={"origin": "http://testserver"})
    assert preview.status_code == 200
    assert "Album" in preview.text
    assert "name='price' type='number' min='0' value='' required" in preview.text
    bad = {"token": "bad", "shop": "UMS", "line_name": "Bela", "order_date": "2026-11-26", "shipping": "", "dn": "DN1", "link": "https://example.com/a", "name": "Album", "price": "", "quantity": "1"}
    assert browser.post("/admin/confirm", data=bad, headers={"origin": "http://testserver"}).status_code == 400
