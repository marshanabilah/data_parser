import json
from types import SimpleNamespace

import pytest

from dashboard_parser import parse_dashboard_order
from order_parser import OrderParseError


class Client:
    def __init__(self, data):
        self.messages = self
        self.data = data

    def create(self, **kwargs):
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(self.data))])


def test_shop_and_links_are_parsed_without_changing_url():
    client = Client({"shop": "New shop", "links": [{"index": 0, "dn_line": "DN-1"}]})
    shop, links = parse_dashboard_order("DN-1 https://example.com/item", client)
    assert shop == "New shop"
    assert links[0].link == "https://example.com/item"


def test_unknown_shop_is_left_for_admin():
    client = Client({"shop": None, "links": [{"index": 0, "dn_line": "DN-1"}]})
    assert parse_dashboard_order("DN-1 https://example.com/item", client)[0] is None


def test_rejects_unmapped_link():
    client = Client({"shop": "Shop", "links": [{"index": 2, "dn_line": "DN-1"}]})
    with pytest.raises(OrderParseError):
        parse_dashboard_order("DN-1 https://example.com/item", client)
