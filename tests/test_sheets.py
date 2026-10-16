import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from models import OrderDraft
from sheets import ORDER_HEADERS, OrdersRepository, SheetSchemaError, WIB


class FakeWorksheet:
    def __init__(self, title, rows=None):
        self.title = title
        self.rows = [list(row) for row in (rows or [])]
        self.hidden = False

    def row_values(self, index):
        return list(self.rows[index - 1]) if len(self.rows) >= index else []

    def get_all_values(self):
        return [list(row) for row in self.rows]

    def update(self, values, range_name):
        assert range_name.startswith("A1:")
        self.rows = [list(row) for row in values]

    def hide(self):
        self.hidden = True


class FakeSpreadsheet:
    def __init__(self, sheets=None):
        self.sheets = {sheet.title: sheet for sheet in (sheets or [])}
        self.batch_calls = []

    def worksheets(self):
        return list(self.sheets.values())

    def add_worksheet(self, title, rows, cols):
        sheet = FakeWorksheet(title)
        self.sheets[title] = sheet
        return sheet

    def values_batch_update(self, body):
        self.batch_calls.append(body)
        for update in body["data"]:
            match = re.fullmatch(r"'([^']+)'!A(\d+):[A-Z](\d+)", update["range"])
            assert match
            title, start, _ = match.groups()
            sheet = self.sheets[title]
            start_index = int(start) - 1
            while len(sheet.rows) < start_index:
                sheet.rows.append([])
            for offset, row in enumerate(update["values"]):
                index = start_index + offset
                if index < len(sheet.rows):
                    sheet.rows[index] = list(row)
                else:
                    sheet.rows.append(list(row))


def draft(number):
    return OrderDraft(
        dn_line=f"DN-{number:03d}",
        items=f"Album {number}",
        link=f"https://example.com/product/{number}",
        nominal="JPY 4,500" if number % 2 else None,
    )


def repository(spreadsheet):
    return OrdersRepository(
        spreadsheet,
        now=lambda: datetime(2026, 10, 16, 14, 30, tzinfo=WIB),
    )


def test_creates_only_fixed_orders_and_hidden_config_tabs():
    sales = FakeWorksheet("Sales", [["legacy"]])
    spreadsheet = FakeSpreadsheet([sales])

    repository(spreadsheet).ensure_schema()

    assert spreadsheet.sheets["Orders"].rows == [list(ORDER_HEADERS)]
    assert spreadsheet.sheets["_BotConfig"].rows == [["Setting", "Value"]]
    assert spreadsheet.sheets["_BotConfig"].hidden is True
    assert sales.rows == [["legacy"]]


def test_rejects_an_existing_orders_tab_with_wrong_headers():
    spreadsheet = FakeSpreadsheet([FakeWorksheet("Orders", [["Wrong"]])])

    with pytest.raises(SheetSchemaError, match="required schema"):
        repository(spreadsheet).ensure_schema()


def test_save_uses_one_batch_for_rows_sequence_and_submission_token():
    spreadsheet = FakeSpreadsheet()
    repo = repository(spreadsheet)

    saved = repo.save_orders((draft(1), draft(2)), "token-1")

    assert [order.order_no for order in saved] == [1, 2]
    assert saved[0].date == "2026-10-16 14:30"
    assert saved[1].nominal is None
    assert len(spreadsheet.batch_calls) == 1
    assert len(spreadsheet.batch_calls[0]["data"]) == 3
    assert spreadsheet.sheets["Orders"].rows[1] == saved[0].as_sheet_row()
    assert spreadsheet.sheets["_BotConfig"].rows[1] == ["next_order_no", 3]
    assert spreadsheet.sheets["_BotConfig"].rows[2][0] == "submission:token-1"


def test_retry_returns_original_rows_without_another_write():
    spreadsheet = FakeSpreadsheet()
    repo = repository(spreadsheet)
    first = repo.save_orders((draft(1),), "same-token")

    second = repo.save_orders((draft(9),), "same-token")

    assert second == first
    assert len(spreadsheet.batch_calls) == 1
    assert len(spreadsheet.sheets["Orders"].rows) == 2


def test_sequence_starts_after_highest_existing_id_and_never_uses_row_number():
    existing = FakeWorksheet(
        "Orders",
        [list(ORDER_HEADERS), ["date", "41", "DN", "Item", "https://x.example", ""]],
    )
    spreadsheet = FakeSpreadsheet([existing])

    saved = repository(spreadsheet).save_orders((draft(1),), "token")

    assert saved[0].order_no == 42


def test_duplicate_lookup_reports_only_existing_requested_links():
    existing = FakeWorksheet(
        "Orders",
        [
            list(ORDER_HEADERS),
            ["date", 1, "DN", "Item", draft(1).link, "", "", "Ordered", ""],
        ],
    )
    spreadsheet = FakeSpreadsheet([existing])

    duplicates = repository(spreadsheet).find_duplicate_links(
        [draft(1).link, draft(2).link]
    )

    assert duplicates == frozenset({draft(1).link})


def test_concurrent_saves_receive_non_overlapping_order_ranges():
    spreadsheet = FakeSpreadsheet()
    repo = repository(spreadsheet)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(repo.save_orders, (draft(1), draft(2)), "token-a"),
            pool.submit(repo.save_orders, (draft(3), draft(4)), "token-b"),
        ]
    order_numbers = sorted(
        order.order_no for future in futures for order in future.result()
    )

    assert order_numbers == [1, 2, 3, 4]
    assert len(spreadsheet.batch_calls) == 2
