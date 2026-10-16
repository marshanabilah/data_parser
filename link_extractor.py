import asyncio
import ipaddress
import json
import socket
from typing import Any, Awaitable, Callable, Iterable, Optional, Protocol, Tuple
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

from models import ExtractedProduct, OrderValidationError


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_FETCH_SECONDS = 10.0
MAX_FALLBACK_TEXT = 12_000
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class ProductFetchError(ValueError):
    """Raised when a product page cannot be fetched or safely interpreted."""


class UnsafeUrlError(ProductFetchError):
    pass


class RedirectLimitError(ProductFetchError):
    pass


class ContentTooLargeError(ProductFetchError):
    pass


class InvalidContentError(ProductFetchError):
    pass


class FetchTimeoutError(ProductFetchError):
    pass


class ItemNameFallback(Protocol):
    def extract_item_name(self, page_text: str) -> str:
        ...


Resolver = Callable[[str], Awaitable[Tuple[str, ...]]]


async def resolve_host(hostname: str) -> Tuple[str, ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        hostname,
        None,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return tuple(dict.fromkeys(record[4][0] for record in records))


async def extract_product_metadata(
    url: str,
    *,
    client: httpx.AsyncClient,
    resolver: Resolver = resolve_host,
    item_fallback: Optional[ItemNameFallback] = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
    max_redirects: int = MAX_REDIRECTS,
    timeout_seconds: float = MAX_FETCH_SECONDS,
) -> ExtractedProduct:
    try:
        html = await asyncio.wait_for(
            _fetch_html(
                url,
                client=client,
                resolver=resolver,
                max_bytes=max_bytes,
                max_redirects=max_redirects,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise FetchTimeoutError("The product page timed out") from exc
    soup = BeautifulSoup(html, "html.parser")

    structured_items, structured_nominal = _json_ld_metadata(soup)
    items = structured_items or _open_graph_title(soup) or _page_title(soup)
    nominal = structured_nominal or _open_graph_price(soup)

    if not items and item_fallback is not None:
        page_text = _clean_page_text(soup)
        if page_text:
            candidate = await asyncio.to_thread(
                item_fallback.extract_item_name,
                page_text,
            )
            items = candidate.strip() or None

    try:
        return ExtractedProduct(items=items, nominal=nominal)
    except OrderValidationError as exc:
        raise InvalidContentError(str(exc)) from exc


async def _fetch_html(
    url: str,
    *,
    client: httpx.AsyncClient,
    resolver: Resolver,
    max_bytes: int,
    max_redirects: int,
) -> str:
    current_url = url
    redirect_count = 0

    while True:
        await _validate_public_https_url(current_url, resolver)
        try:
            request = client.build_request(
                "GET",
                current_url,
                headers={
                    "accept": "text/html,application/xhtml+xml",
                    "user-agent": "OrderLinkBot/1.0",
                },
            )
            for sensitive_header in ("authorization", "cookie", "proxy-authorization"):
                request.headers.pop(sensitive_header, None)
            response = await client.send(request, stream=True, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise FetchTimeoutError("The product page timed out") from exc
        except httpx.HTTPError as exc:
            raise ProductFetchError("The product page could not be fetched") from exc

        try:
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise InvalidContentError("Redirect response has no destination")
                if redirect_count >= max_redirects:
                    raise RedirectLimitError("The product page redirected too many times")
                current_url = urljoin(current_url, location)
                redirect_count += 1
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ProductFetchError(
                    f"The product page returned HTTP {response.status_code}"
                ) from exc

            content_type = response.headers.get("content-type", "")
            if content_type and not _is_html_content_type(content_type):
                raise InvalidContentError("The product URL did not return HTML")

            declared_size = response.headers.get("content-length")
            if declared_size and _declared_size_exceeds(declared_size, max_bytes):
                raise ContentTooLargeError("The product page is too large")

            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ContentTooLargeError("The product page is too large")
            return bytes(body).decode(response.encoding or "utf-8", errors="replace")
        finally:
            await response.aclose()


async def _validate_public_https_url(url: str, resolver: Resolver) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise UnsafeUrlError("Only public HTTPS product URLs are allowed")

    try:
        literal = ipaddress.ip_address(parsed.hostname)
        addresses = (str(literal),)
    except ValueError:
        try:
            addresses = await resolver(parsed.hostname)
        except (OSError, socket.gaierror) as exc:
            raise UnsafeUrlError("The product hostname could not be resolved") from exc

    if not addresses:
        raise UnsafeUrlError("The product hostname could not be resolved")
    try:
        parsed_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
    except ValueError as exc:
        raise UnsafeUrlError("The product hostname resolved unexpectedly") from exc
    if any(not address.is_global for address in parsed_addresses):
        raise UnsafeUrlError("The product URL resolves to a non-public address")


def _is_html_content_type(value: str) -> bool:
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type in {"text/html", "application/xhtml+xml"}


def _declared_size_exceeds(value: str, maximum: int) -> bool:
    try:
        return int(value) > maximum
    except ValueError:
        return False


def _json_ld_metadata(soup: BeautifulSoup) -> Tuple[Optional[str], Optional[str]]:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk_json(payload):
            if not isinstance(node, dict) or not _is_product(node.get("@type")):
                continue
            name = _clean_optional(node.get("name"))
            price = _offer_price(node.get("offers"))
            if name or price:
                return name, price
    return None, None


def _walk_json(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _is_product(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower() == "product"
    if isinstance(value, list):
        return any(_is_product(item) for item in value)
    return False


def _offer_price(offers: Any) -> Optional[str]:
    candidates = offers if isinstance(offers, list) else [offers]
    for offer in candidates:
        if not isinstance(offer, dict):
            continue
        amount = _clean_optional(offer.get("price"))
        currency = _clean_optional(offer.get("priceCurrency"))
        if amount:
            return f"{currency} {amount}" if currency else amount
    return None


def _open_graph_title(soup: BeautifulSoup) -> Optional[str]:
    return _meta_content(soup, "og:title")


def _open_graph_price(soup: BeautifulSoup) -> Optional[str]:
    amount = _meta_content(soup, "product:price:amount") or _meta_content(
        soup,
        "og:price:amount",
    )
    currency = _meta_content(soup, "product:price:currency") or _meta_content(
        soup,
        "og:price:currency",
    )
    if not amount:
        return None
    return f"{currency} {amount}" if currency else amount


def _meta_content(soup: BeautifulSoup, property_name: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": property_name})
    if tag is None:
        tag = soup.find("meta", attrs={"name": property_name})
    return _clean_optional(tag.get("content")) if tag else None


def _page_title(soup: BeautifulSoup) -> Optional[str]:
    heading = soup.find("h1")
    if heading:
        value = _clean_optional(heading.get_text(" ", strip=True))
        if value:
            return value
    if soup.title:
        return _clean_optional(soup.title.get_text(" ", strip=True))
    return None


def _clean_page_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())[:MAX_FALLBACK_TEXT]


def _clean_optional(value: Any) -> Optional[str]:
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None
