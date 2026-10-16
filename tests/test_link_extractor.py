import asyncio
import json
import socket

import httpx
import pytest

from link_extractor import (
    ContentTooLargeError,
    FetchTimeoutError,
    InvalidContentError,
    RedirectLimitError,
    UnsafeUrlError,
    extract_product_metadata,
)


PUBLIC_IPS = ("93.184.216.34",)


async def public_resolver(hostname: str) -> tuple[str, ...]:
    assert hostname
    return PUBLIC_IPS


def run_extract(
    handler,
    url: str = "https://shop.example/product",
    **kwargs,
):
    async def execute():
        client_headers = kwargs.pop("client_headers", None)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers=client_headers,
        ) as client:
            return await extract_product_metadata(
                url,
                client=client,
                resolver=kwargs.pop("resolver", public_resolver),
                **kwargs,
            )

    return asyncio.run(execute())


def html_response(html: str) -> httpx.Response:
    return httpx.Response(200, text=html, headers={"content-type": "text/html"})


def test_json_ld_has_priority_and_preserves_currency_text():
    payload = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Structured Album",
        "offers": {"@type": "Offer", "price": "4,500", "priceCurrency": "JPY"},
    }
    html = f"""
        <meta property="og:title" content="Open Graph Album">
        <meta property="product:price:amount" content="9999">
        <meta property="product:price:currency" content="IDR">
        <script type="application/ld+json">{json.dumps(payload)}</script>
        <h1>Heading Album</h1>
    """

    result = run_extract(lambda request: html_response(html))

    assert result.items == "Structured Album"
    assert result.nominal == "JPY 4,500"


def test_open_graph_then_heading_and_title_fallbacks():
    og = """
        <meta property="og:title" content="OG Product">
        <meta property="product:price:amount" content="29.90">
        <meta property="product:price:currency" content="USD">
    """
    result = run_extract(lambda request: html_response(og))
    assert result.items == "OG Product"
    assert result.nominal == "USD 29.90"

    heading = run_extract(
        lambda request: html_response("<h1> Main Product </h1>")
    )
    assert heading.items == "Main Product"
    assert heading.nominal is None

    title = run_extract(
        lambda request: html_response("<title>Fallback Product</title>")
    )
    assert title.items == "Fallback Product"


def test_bounded_ai_fallback_receives_clean_text_only():
    class Fallback:
        def __init__(self):
            self.text = ""

        def extract_item_name(self, text: str) -> str:
            self.text = text
            return "AI Product"

    fallback = Fallback()
    html = "<script>ignore me</script><p>Useful product details</p>"
    result = run_extract(
        lambda request: html_response(html),
        item_fallback=fallback,
    )

    assert result.items == "AI Product"
    assert fallback.text == "Useful product details"


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.example/product",
        "https://user:secret@shop.example/product",
        "https://127.0.0.1/product",
        "https://[::1]/product",
    ],
)
def test_rejects_unsafe_url_before_request(url):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text="<h1>Never</h1>")

    with pytest.raises(UnsafeUrlError):
        run_extract(handler, url=url)
    assert calls == []


def test_rejects_hostname_when_any_dns_result_is_not_public():
    calls = []

    async def mixed_resolver(hostname):
        return ("93.184.216.34", "10.0.0.2")

    with pytest.raises(UnsafeUrlError):
        run_extract(
            lambda request: calls.append(request),
            resolver=mixed_resolver,
        )
    assert calls == []


def test_revalidates_each_redirect_and_does_not_follow_unsafe_target():
    calls = []

    async def resolver(hostname):
        return {
            "shop.example": PUBLIC_IPS,
            "cdn.example": PUBLIC_IPS,
            "internal.example": ("192.168.1.5",),
        }[hostname]

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == "shop.example":
            return httpx.Response(302, headers={"location": "https://cdn.example/item"})
        return httpx.Response(302, headers={"location": "https://internal.example/secret"})

    with pytest.raises(UnsafeUrlError):
        run_extract(handler, resolver=resolver)
    assert calls == ["https://shop.example/product", "https://cdn.example/item"]


def test_redirect_limit_is_enforced():
    def handler(request):
        path_number = int(request.url.path.rsplit("/", 1)[-1] or 0)
        return httpx.Response(302, headers={"location": f"/product/{path_number + 1}"})

    with pytest.raises(RedirectLimitError):
        run_extract(handler, url="https://shop.example/product/0", max_redirects=2)


def test_rejects_non_html_and_oversized_bodies():
    with pytest.raises(InvalidContentError):
        run_extract(
            lambda request: httpx.Response(
                200,
                content=b"{}",
                headers={"content-type": "application/json"},
            )
        )

    with pytest.raises(ContentTooLargeError):
        run_extract(
            lambda request: httpx.Response(
                200,
                content=b"x" * 11,
                headers={"content-type": "text/html"},
            ),
            max_bytes=10,
        )


def test_timeout_becomes_typed_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(FetchTimeoutError):
        run_extract(handler)


def test_sensitive_client_headers_are_not_forwarded():
    seen_headers = None

    def handler(request):
        nonlocal seen_headers
        seen_headers = request.headers
        return html_response("<h1>Public Product</h1>")

    run_extract(
        handler,
        client_headers={"authorization": "secret", "cookie": "session=secret"},
    )

    assert "authorization" not in seen_headers
    assert "cookie" not in seen_headers


def test_dns_resolution_failure_is_safe():
    async def failed_resolver(hostname):
        raise socket.gaierror("not found")

    with pytest.raises(UnsafeUrlError):
        run_extract(lambda request: httpx.Response(200), resolver=failed_resolver)
