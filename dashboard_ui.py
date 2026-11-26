"""Shared page shell for the local dashboard."""

import html
from pathlib import Path

from starlette.responses import HTMLResponse

STYLE = Path(__file__).with_name("dashboard_style.css").read_text(encoding="utf-8")


def page(content: str, *, title: str, active: str = "", status_code: int = 200, public: bool = False) -> HTMLResponse:
    brand = '<span class="brand">nitipyuq<small>jastip order desk</small></span>'
    if public:
        body = f'<main class="auth-wrap"><div class="auth-card public-card">{brand}{content}</div></main>'
    else:
        links = (("overview", "/admin", "Overview"), ("orders", "/admin/orders", "Orders"), ("intake", "/admin/intake", "Parse an order"), ("shops", "/admin/shops", "Shop settings"))
        nav = "".join(f'<a href="{url}"{active_attr(key, active)}>{label}</a>' for key, url, label in links)
        body = f'<div class="app"><aside class="sidebar">{brand}<div><p class="nav-title">Workspace</p><nav class="nav" aria-label="Admin navigation">{nav}</nav></div><div class="sidebar-note"><strong>Made for your jastip</strong>Keep every order in its place.</div></aside><main class="main"><header class="topbar"><span>Order management</span><strong>Admin workspace</strong></header><div class="content">{content}</div></main></div>'
    return HTMLResponse(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)} · nitipyuq</title><style>{STYLE}</style></head><body>{body}</body></html>', status_code=status_code)


def active_attr(key: str, active: str) -> str:
    return ' aria-current="page"' if key == active else ""
