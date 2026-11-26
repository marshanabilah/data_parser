import json
import httpx
import pytest

from dashboard_parser import parse_dashboard_order
from order_parser import OrderParseError


def transport(data, status=200):
    def handler(request):
        assert request.url.host == "openrouter.ai"
        assert json.loads(request.content)["model"] == "openrouter/free"
        return httpx.Response(status, json={"choices": [{"message": {"content": json.dumps(data)}}]})
    return httpx.MockTransport(handler)


def test_shop_and_links_are_parsed_without_changing_url():
    client = transport({"shop": "New shop", "links": [{"index": 0, "dn_line": "DN-1"}]})
    shop, links = parse_dashboard_order("DN-1 https://example.com/item", "test", client)
    assert shop == "New shop"
    assert links[0].link == "https://example.com/item"


def test_unknown_shop_is_left_for_admin():
    client = transport({"shop": None, "links": [{"index": 0, "dn_line": "DN-1"}]})
    assert parse_dashboard_order("DN-1 https://example.com/item", "test", client)[0] is None


def test_rejects_unmapped_link():
    client = transport({"shop": "Shop", "links": [{"index": 2, "dn_line": "DN-1"}]})
    with pytest.raises(OrderParseError):
        parse_dashboard_order("DN-1 https://example.com/item", "test", client)


def test_rate_limit_has_actionable_error():
    with pytest.raises(OrderParseError, match="free request limit"):
        parse_dashboard_order("DN-1 https://example.com/item", "test", transport({}, 429))
