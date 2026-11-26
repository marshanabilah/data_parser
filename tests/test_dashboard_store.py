from datetime import date

import pytest

from dashboard_orders import OrderRecord, ProductLine
from dashboard_store import DashboardStore


def test_save_retry_and_settings_persist(tmp_path):
    path = str(tmp_path / "orders.sqlite")
    store = DashboardStore(path)
    store.set_shop("UMS", True, 500)
    order = OrderRecord(date(2026, 11, 26), "Name", "DN", "UMS", True, 500, (ProductLine("Album", "https://example.com/a", 100, 2),))
    ids = store.save("token", [order])
    assert store.save("token", [order]) == ids
    store.set_shop("UMS", True, 900)
    reopened = DashboardStore(path)
    assert reopened.search("Name")[0][5] == 500
    assert reopened.products(ids[0])[0][3] == 2
    assert reopened.set_status(ids[0], "Completed")
    assert reopened.set_status(ids[0], "Purchased")


def test_invalid_batch_rolls_back(tmp_path):
    store = DashboardStore(str(tmp_path / "orders.sqlite"))
    line = ProductLine("Album", "https://example.com/a", 100, 1)
    good = OrderRecord(date.today(), "Name", "DN", "Mercari", False, None, (line,))
    bad = OrderRecord(date.today(), "Name", "DN", "Unknown", False, None, (line,))
    with pytest.raises(ValueError):
        store.save("batch", [good, bad])
    assert store.search() == []
    assert len(store.save("batch", [good])) == 1


def test_duplicate_lookup_includes_each_imported_link(tmp_path):
    from csv_orders import parse_csv_orders
    store = DashboardStore(str(tmp_path / "orders.sqlite"))
    text = ('Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n'
            '26/2,1,A,Items,"https://example.com/a\nhttps://example.com/b",600,,Ordered,\n')
    store.import_orders(parse_csv_orders(text))
    assert store.has_link("https://example.com/a")
    assert store.has_link("https://example.com/b")
    assert not store.has_link("https://example.com/c")


def test_legacy_invalid_price_does_not_block_status_update(tmp_path):
    from csv_orders import parse_csv_orders
    store = DashboardStore(str(tmp_path / "orders.sqlite"))
    text = ('Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n'
            '26/2,1,A,Item,https://example.com/a,to be confirmed,,Ordered,\n')
    store.import_orders(parse_csv_orders(text))
    assert store.update_imported(1, "", "to be confirmed", status="Completed")
    assert store.imported()[0][1]["_current_status"] == "Completed"
    with pytest.raises(ValueError):
        store.update_imported(1, "", "bad new price", status="Purchased")
