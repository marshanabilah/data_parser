import pytest

from csv_orders import parse_csv_orders
from dashboard_store import DashboardStore


def test_shop_matching_and_backfill_preserves_manual_choice(tmp_path):
    store = DashboardStore(str(tmp_path / "test.sqlite"))
    text = ("Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n"
            "26/2,1,A,Album,https://jp.mercari.com/item/a,,,Ordered,\n"
            "26/2,2,B,Album,https://shop.example.com/item/a,600,,Ordered,\n"
            "26/2,3,C,Album,https://mercari.com.evil.example.com/item/a,600,,Ordered,\n")
    store.import_orders(parse_csv_orders(text))
    found = {row[0]: row[2] for row in store.imported()}
    assert found == {1: "Mercari", 2: "", 3: ""}
    store.update_imported(2, "Rakuten", "600")
    store.set_shop("New shop", True, None, domains=["example.com"])
    found = {row[0]: row[2] for row in store.imported()}
    assert found[2] == "Rakuten"
    assert found[3] == "New shop"
    assert {row[0]: row[2] for row in DashboardStore(store.path).imported()} == found


def test_duplicate_domain_rejected_without_changing_shop(tmp_path):
    store = DashboardStore(str(tmp_path / "test.sqlite"))
    with pytest.raises(ValueError, match="already assigned"):
        store.set_shop("Other", True, 50, domains=["mercari.com"])
    assert "Other" not in [name for name, _, _ in store.shops()]
