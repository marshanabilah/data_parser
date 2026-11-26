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
    assert "Bela" in browser.get("/admin/orders?q=Bela").text
    browser.post("/admin/orders", data={"id": "1", "status": "Completed"}, headers={"origin": "http://testserver"})
    assert "selected>Completed" in browser.get("/admin/orders").text


def test_intake_parses_preview_and_requires_price(client, monkeypatch):
    from models import ExtractedProduct, SubmittedLink
    import dashboard

    browser, _, _ = client
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(dashboard, "parse_dashboard_order", lambda *args, **kwargs: ("UMS", (SubmittedLink(dn_line="DN1", link="https://example.com/a"),)))

    async def extract(_self, _link):
        return ExtractedProduct(items="Album", nominal=None)

    monkeypatch.setattr(dashboard.PublicProductExtractor, "extract", extract)
    browser.post("/login", data={"password": "correct horse"}, headers={"origin": "http://testserver"})
    assert "name='shop'" not in browser.get("/admin/intake").text
    preview = browser.post("/admin/intake", data={"line_name": "Bela", "message": "DN1 https://example.com/a"}, headers={"origin": "http://testserver"})
    assert preview.status_code == 200
    assert "Album" in preview.text
    assert "value='UMS'" in preview.text
    assert "name='price' type='number' min='0' value='' required" in preview.text
    bad = {"token": "bad", "shop": "UMS", "line_name": "Bela", "order_date": "2026-11-26", "shipping": "", "dn": "DN1", "link": "https://example.com/a", "name": "Album", "price": "", "quantity": "1"}
    assert browser.post("/admin/confirm", data=bad, headers={"origin": "http://testserver"}).status_code == 400


def test_new_shop_is_saved_with_confirmed_order(client):
    browser, _, _ = client
    browser.post("/login", data={"password": "correct horse"}, headers={"origin": "http://testserver"})
    payload = {"token": "new-shop", "shop": "New Shop", "allows_multiple": "1", "line_name": "Bela", "order_date": "2026-11-26", "shipping": "", "dn": ["DN1", "DN1"], "link": ["https://example.com/a", "https://example.com/b"], "name": ["A", "B"], "price": ["100", "200"], "quantity": ["1", "2"]}
    assert "Item IDs: 1" in browser.post("/admin/confirm", data=payload, headers={"origin": "http://testserver"}).text
    shops = browser.get("/admin/shops").text
    assert "New Shop" in shops
    assert "<table class='shop-table'>" in shops


def test_csv_preview_import_and_duplicate_protection(client):
    browser, _, _ = client
    csv_text = "Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n2026-10-16,57,Bela,Album,https://example.com/a,,,Ordered,Box 1\n"
    headers = {"origin": "http://testserver"}
    assert browser.get("/admin/import", follow_redirects=False).status_code == 303
    browser.post("/login", data={"password": "correct horse"}, headers=headers)
    preview = browser.post("/admin/import", data={"csv": csv_text}, headers=headers)
    assert preview.status_code == 200
    assert "Needs review" in preview.text
    assert "Bela" not in browser.get("/admin/orders").text
    saved = browser.post("/admin/import", data={"csv": csv_text, "confirm": "yes"}, headers=headers)
    assert "1 orders saved" in saved.text
    assert "Box 1" in browser.get("/admin/orders?q=Bela").text
    assert browser.post("/admin/import", data={"csv": csv_text, "confirm": "yes"}, headers=headers).status_code == 400


def test_order_records_review_tab_and_count(client):
    browser, _, _ = client
    headers = {"origin": "http://testserver"}
    csv_text = ("Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n"
                "26/2,57,Bela,Album,https://example.com/a,600,,Ordered,\n"
                "27/2,58,Ani,Photocard,https://example.com/b,,,Ordered,\n")
    browser.post("/login", data={"password": "correct horse"}, headers=headers)
    browser.post("/admin/import", data={"csv": csv_text, "confirm": "yes"}, headers=headers)
    all_orders = browser.get("/admin/orders").text
    assert "<table class='orders-table'>" in all_orders
    assert "<th>Link</th>" in all_orders
    assert "href='https://example.com/a'" in all_orders
    assert "View original sheet details" in all_orders
    assert "Needs review <span class='review-count'>2</span>" in all_orders
    review = browser.get("/admin/orders?view=review").text
    assert "Album" in review and "Photocard" in review
    update = {"kind": "imported", "id": "57", "shop": "Mercari", "nominal": "600", "view": "review"}
    response = browser.post("/admin/orders", data=update, headers=headers, follow_redirects=False)
    assert response.headers["location"] == "/admin/orders?saved=1&view=review"
    review = browser.get(response.headers["location"]).text
    assert "Needs review <span class='review-count'>1</span>" in review
    assert "Saved successfully" in review
    assert "Album" not in review and "Photocard" in review
    assert "Album" in browser.get("/admin/orders").text


def test_known_shop_without_price_is_not_in_shop_review(client):
    browser, _, _ = client
    headers = {"origin": "http://testserver"}
    csv_text = ("Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n"
                "26/2,77,Bela,Album,https://jp.mercari.com/item/a,,,Ordered,\n")
    browser.post("/login", data={"password": "correct horse"}, headers=headers)
    browser.post("/admin/import", data={"csv": csv_text, "confirm": "yes"}, headers=headers)
    orders = browser.get("/admin/orders").text
    assert "Needs review <span class='review-count'>0</span>" in orders
    assert "Price missing or invalid" in orders
    assert "value='Mercari'" in orders


def test_review_can_create_shop_and_match_other_orders(client):
    browser, _, _ = client
    headers = {"origin": "http://testserver"}
    csv_text = ("Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n"
                "26/2,57,Bela,Album,https://paypayfleamarket.yahoo.co.jp/a,600,,Ordered,\n"
                "27/2,58,Ani,Card,https://paypayfleamarket.yahoo.co.jp/b,700,,Ordered,\n")
    browser.post("/login", data={"password": "correct horse"}, headers=headers)
    browser.post("/admin/import", data={"csv": csv_text, "confirm": "yes"}, headers=headers)
    payload = {"kind": "imported", "id": "57", "shop": "PayPay", "nominal": "600", "view": "review", "remember_domain": "yes"}
    rejected = browser.post("/admin/orders", data=payload, headers=headers)
    assert rejected.status_code == 400
    assert "Choose whether it allows" in rejected.text
    assert "PayPay" not in browser.get("/admin/shops").text
    payload["new_rule"] = "0"
    saved = browser.post("/admin/orders", data=payload, headers=headers)
    assert "Saved successfully" in saved.text
    assert "Needs review <span class='review-count'>0</span>" in saved.text
    settings = browser.get("/admin/shops").text
    assert "PayPay" in settings
    assert "paypayfleamarket.yahoo.co.jp" in settings
    orders = browser.get("/admin/orders").text
    assert "<dialog id='order-57'" in orders
    assert "value='PayPay'" in orders


def test_unsafe_imported_link_is_not_clickable(client):
    browser, _, _ = client
    headers = {"origin": "http://testserver"}
    csv_text = ("Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n"
                "26/2,91,Example,Item,javascript:alert(1),600,,Ordered,\n")
    browser.post("/login", data={"password": "correct horse"}, headers=headers)
    browser.post("/admin/import", data={"csv": csv_text, "confirm": "yes"}, headers=headers)
    orders = browser.get("/admin/orders").text
    assert "javascript:alert(1)" in orders
    assert "href='javascript:alert(1)'" not in orders


def test_admin_calculator_rate_persists_and_requires_login(client):
    browser, path, credential = client
    headers = {"origin": "http://testserver"}
    assert browser.get("/admin/calculator", follow_redirects=False).status_code == 303
    browser.post("/login", data={"password": "correct horse"}, headers=headers)
    missing = browser.post("/admin/calculator", data={"action": "calculate", "price": "1000", "quantity": "2"}, headers=headers)
    assert "Set the exchange rate" in missing.text
    assert "Not set" in browser.get("/admin/orders").text
    assert browser.post("/admin/rate", data={"rate": "110"}, headers=headers, follow_redirects=False).status_code == 303
    result = browser.post("/admin/calculator", data={"action": "calculate", "price": "1000", "quantity": "2"}, headers=headers)
    assert "Rp250.000" in result.text
    assert "Shipping is not included" in result.text
    reopened = TestClient(create_app(path, credential, "s" * 32, secure=False))
    reopened.post("/login", data={"password": "correct horse"}, headers=headers)
    assert "Rp110 / ¥1" in reopened.get("/admin/calculator").text
    assert "Rp110 / ¥1" in reopened.get("/admin/orders").text
    assert "value='110'" in reopened.get("/admin/rate").text
    assert browser.post("/admin/rate", data={"rate": "-1"}, headers=headers).status_code == 200
    assert "Rp110 / ¥1" in browser.get("/admin").text
