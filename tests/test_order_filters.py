from datetime import date
import sqlite3
import subprocess
import sys

from order_filters import imported_date, within_dates
from csv_orders import parse_csv_orders
from dashboard_store import DashboardStore


def test_imported_day_month_uses_explicit_assumed_year():
    assert imported_date("26/2", 2026) == date(2026, 2, 26)
    assert imported_date("26/2") is None
    assert imported_date("26/2/2024", 2026) == date(2024, 2, 26)
    assert imported_date("2025-10-16 14:30", 2026) == date(2025, 10, 16)
    assert imported_date("31/2", 2026) is None


def test_date_filter_excludes_unknown_dates_only_when_active():
    assert within_dates(None, None, None)
    assert not within_dates(None, date(2026, 1, 1), None)
    assert within_dates(date(2026, 2, 26), date(2026, 2, 1), date(2026, 2, 28))


def test_assign_year_matches_csv_without_rewriting_originals(tmp_path):
    path = tmp_path / "db.sqlite"
    source = tmp_path / "orders.csv"
    text = ("Date,Order No.,DN Line,Items,Link,Nominal,Arrived Date,Status,Box\n"
            "26/2,77,Bela,Album,https://example.com/a,600,,Ordered,\n")
    source.write_text(text, encoding="utf-8")
    store = DashboardStore(str(path))
    store.import_orders(parse_csv_orders(text), assumed_year=2025)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE imported_orders SET assumed_year = NULL")
    command = [sys.executable, "set_import_year.py", "--database", str(path), "--csv", str(source), "--year", "2026", "--backup-dir", str(tmp_path)]
    subprocess.run(command, check=True, capture_output=True, text=True)
    assert store.imported()[0][1]["_assumed_year"] is None
    subprocess.run([*command, "--apply"], check=True, capture_output=True, text=True)
    row = store.imported()[0][1]
    assert row["_assumed_year"] == 2026
    assert row["Date"] == "26/2"
    assert len(list(tmp_path.glob("nitipyuq-backup-*.sqlite3"))) == 1
