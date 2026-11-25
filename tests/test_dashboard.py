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
