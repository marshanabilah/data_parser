from datetime import date

import pytest

from dashboard_orders import OrderRecord, ProductLine


def product(link="https://example.com/item", price=1200, quantity=1):
    return ProductLine("Album", link, price, quantity)


def record(products, multi=False, shipping=None):
    return OrderRecord(date(2026, 11, 26), "Customer", "DN-1", "Shop", multi, shipping, tuple(products))


def test_multilink_and_optional_shipping():
    order = record([product(), product("https://example.com/other", 500, 2)], multi=True)
    assert len(order.products) == 2
    assert order.shipping_jpy is None
    assert order.status == "Purchased"


@pytest.mark.parametrize("products,multi,shipping", [
    ([product(), product()], False, None),
    ([product()], False, -1),
    ([], True, None),
])
def test_invalid_records_are_rejected(products, multi, shipping):
    with pytest.raises(ValueError):
        record(products, multi, shipping)


def test_price_and_quantity_must_be_integers():
    with pytest.raises(ValueError):
        record([product(price=None)])
    with pytest.raises(ValueError):
        record([product(quantity=True)])
    with pytest.raises(ValueError):
        product(price=-1)
    with pytest.raises(ValueError):
        product(quantity=0)
    with pytest.raises(ValueError):
        product(link="http://example.com")
