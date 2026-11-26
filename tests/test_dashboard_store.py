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
