import pytest
from datetime import date

from csv_orders import parse_csv_orders
from dashboard_store import DashboardStore
from dashboard_orders import OrderRecord, ProductLine

CSV = 'Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n2026-10-16 14:30,23,Bela,Album,https://example.com/a,,2026-10-20,Ordered,Box 1\n'


def test_csv_preserves_and_imports_missing_price(tmp_path):
    rows = parse_csv_orders(CSV)
    store = DashboardStore(str(tmp_path / 'db.sqlite'))
    store.import_orders(rows)
    order_id, original, shop, nominal = store.imported('Bela')[0]
    assert (order_id, shop, nominal) == (23, '', '')
    assert original['Arrived Date'] == '2026-10-20'
    assert original['Box'] == 'Box 1'
    assert store.update_imported(23, 'Mercari', '1200')
    assert store.imported('23')[0][3] == '1200'
    order = OrderRecord(date.today(), 'Bela', 'Bela', 'Mercari', False, None, (ProductLine('Album', 'https://example.com/b', 500, 1),))
    assert store.save('new-order', [order]) == [24]
    with pytest.raises(ValueError, match='already exists'):
        store.import_orders(rows)


def test_csv_rejects_missing_or_duplicate_ids():
    with pytest.raises(ValueError):
        parse_csv_orders(CSV + '2026-10-17,23,Bela,Album,https://example.com/b,500,,,\n')
    with pytest.raises(ValueError):
        parse_csv_orders('wrong,headers\na,b\n')
