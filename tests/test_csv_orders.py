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
    with pytest.raises(ValueError, match='already been imported'):
        store.import_orders(rows)


def test_csv_retains_duplicate_ids_and_extra_columns(tmp_path):
    sample = '8,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box,\n26/2,23,Bela,Album,https://example.com/a,600,,Ordered,,note\n27/2,23,Ani,Album,https://example.com/b,500,,Ordered,,\n'
    rows = parse_csv_orders(sample)
    store = DashboardStore(str(tmp_path / 'db.sqlite'))
    assert store.import_orders(rows) == [23, 24]
    saved = store.imported()
    assert len(saved) == 2
    assert {row[1]['Order No.'] for row in saved} == {'23'}
    assert next(row for row in saved if row[0] == 23)[1]['Extra columns'][0]['value'] == 'note'
    assert next(row for row in saved if row[0] == 23)[1]['Date'] == '26/2'


def test_csv_rejects_wrong_header():
    with pytest.raises(ValueError):
        parse_csv_orders('wrong,headers\na,b\n')


def test_changed_export_with_overlapping_original_number_is_rejected(tmp_path):
    store = DashboardStore(str(tmp_path / 'db.sqlite'))
    store.import_orders(parse_csv_orders(CSV))
    changed = CSV.replace('Ordered', 'Arrived WH JP') + '2026-10-17,24,Ani,Card,https://example.com/b,500,,,\n'
    with pytest.raises(ValueError, match='overlaps'):
        store.import_orders(parse_csv_orders(changed))
    assert len(store.imported()) == 1
