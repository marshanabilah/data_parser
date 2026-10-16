import json
import os
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

import gspread
from google.oauth2.service_account import Credentials

from models import OrderDraft, SavedOrder


SCOPES = (
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
)
ORDERS_TAB = "Orders"
CONFIG_TAB = "_BotConfig"
ORDER_HEADERS = (
    "Date",
    "Order No.",
    "DN Line",
    "Items",
    "Link",
    "Nominal",
    "Arrived Date",
    "Status",
    "Box",
)
CONFIG_HEADERS = ("Setting", "Value")
NEXT_ORDER_KEY = "next_order_no"
SUBMISSION_KEY_PREFIX = "submission:"
WIB = ZoneInfo("Asia/Jakarta")


class SheetRepositoryError(RuntimeError):
    pass


class SheetSchemaError(SheetRepositoryError):
    pass


def _parse_credentials(raw: str) -> Dict[str, Any]:
    try:
        credentials = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SheetRepositoryError("Google credentials are not valid JSON") from exc
    private_key = credentials.get("private_key")
    if isinstance(private_key, str):
        credentials["private_key"] = private_key.replace("\\n", "\n")
    return credentials


def spreadsheet_from_environment() -> Any:
    spreadsheet_id = os.environ.get("SPREADSHEET_ID", "").strip()
    raw_credentials = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not spreadsheet_id or not raw_credentials:
        raise SheetRepositoryError(
            "SPREADSHEET_ID and GOOGLE_CREDENTIALS_JSON are required"
        )
    credentials = Credentials.from_service_account_info(
        _parse_credentials(raw_credentials),
        scopes=SCOPES,
    )
    return gspread.authorize(credentials).open_by_key(spreadsheet_id)


class OrdersRepository:
    def __init__(
        self,
        spreadsheet: Any,
        now: Callable[[], datetime] = lambda: datetime.now(WIB),
    ) -> None:
        self._spreadsheet = spreadsheet
        self._now = now
        self._lock = threading.Lock()

    def ensure_schema(self) -> None:
        with self._lock:
            self._ensure_schema_unlocked()

    def find_duplicate_links(self, links: Iterable[str]) -> frozenset[str]:
        requested = frozenset(links)
        if not requested:
            return frozenset()
        with self._lock:
            orders, _ = self._ensure_schema_unlocked()
            rows = orders.get_all_values()
            existing = {row[4] for row in rows[1:] if len(row) > 4 and row[4]}
            return frozenset(existing & requested)

    def save_orders(
        self,
        drafts: Sequence[OrderDraft],
        submission_token: str,
    ) -> Tuple[SavedOrder, ...]:
        if not drafts:
            raise SheetRepositoryError("At least one order is required")
        token = submission_token.strip()
        if not token:
            raise SheetRepositoryError("Submission token is required")

        with self._lock:
            orders, config = self._ensure_schema_unlocked()
            config_rows = config.get_all_values()
            settings = _settings_by_key(config_rows)
            prior = settings.get(f"{SUBMISSION_KEY_PREFIX}{token}")
            if prior is not None:
                return _decode_saved_orders(prior)

            order_rows = orders.get_all_values()
            next_order_no = _next_order_number(settings, order_rows)
            timestamp = self._now().astimezone(WIB).strftime("%Y-%m-%d %H:%M")
            saved = tuple(
                SavedOrder(
                    date=timestamp,
                    order_no=next_order_no + index,
                    dn_line=draft.dn_line,
                    items=draft.items,
                    link=draft.link,
                    nominal=draft.nominal,
                )
                for index, draft in enumerate(drafts)
            )

            order_start = len(order_rows) + 1
            order_end = order_start + len(saved) - 1
            next_key_row = _setting_row(config_rows, NEXT_ORDER_KEY)
            if next_key_row is None:
                next_key_row = len(config_rows) + 1
            token_row = max(len(config_rows), next_key_row) + 1

            body = {
                "valueInputOption": "USER_ENTERED",
                "data": [
                    {
                        "range": f"'{ORDERS_TAB}'!A{order_start}:I{order_end}",
                        "values": [order.as_sheet_row() for order in saved],
                    },
                    {
                        "range": f"'{CONFIG_TAB}'!A{next_key_row}:B{next_key_row}",
                        "values": [[NEXT_ORDER_KEY, next_order_no + len(saved)]],
                    },
                    {
                        "range": f"'{CONFIG_TAB}'!A{token_row}:B{token_row}",
                        "values": [
                            [
                                f"{SUBMISSION_KEY_PREFIX}{token}",
                                json.dumps(
                                    [order.as_sheet_row() for order in saved],
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                            ]
                        ],
                    },
                ],
            }
            self._spreadsheet.values_batch_update(body)
            return saved

    def _ensure_schema_unlocked(self) -> Tuple[Any, Any]:
        worksheets = {sheet.title: sheet for sheet in self._spreadsheet.worksheets()}
        orders = worksheets.get(ORDERS_TAB)
        if orders is None:
            orders = self._spreadsheet.add_worksheet(
                title=ORDERS_TAB,
                rows=1000,
                cols=len(ORDER_HEADERS),
            )
            orders.update([list(ORDER_HEADERS)], range_name="A1:I1")
        elif tuple(orders.row_values(1)) != ORDER_HEADERS:
            raise SheetSchemaError(
                "Orders worksheet headers do not match the required schema"
            )

        config = worksheets.get(CONFIG_TAB)
        if config is None:
            config = self._spreadsheet.add_worksheet(
                title=CONFIG_TAB,
                rows=1000,
                cols=len(CONFIG_HEADERS),
            )
            config.update([list(CONFIG_HEADERS)], range_name="A1:B1")
            config.hide()
        elif tuple(config.row_values(1)) != CONFIG_HEADERS:
            raise SheetSchemaError("_BotConfig headers do not match the required schema")
        return orders, config


def _settings_by_key(rows: Sequence[Sequence[str]]) -> Dict[str, str]:
    return {row[0]: row[1] for row in rows[1:] if len(row) >= 2 and row[0]}


def _setting_row(rows: Sequence[Sequence[str]], key: str) -> Optional[int]:
    for index, row in enumerate(rows[1:], start=2):
        if row and row[0] == key:
            return index
    return None


def _next_order_number(
    settings: Dict[str, str],
    order_rows: Sequence[Sequence[str]],
) -> int:
    configured = settings.get(NEXT_ORDER_KEY)
    if configured is not None:
        try:
            value = int(configured)
        except (TypeError, ValueError) as exc:
            raise SheetRepositoryError("next_order_no is not a valid integer") from exc
        if value < 1:
            raise SheetRepositoryError("next_order_no must be positive")
        return value

    existing = []
    for row in order_rows[1:]:
        if len(row) > 1:
            try:
                existing.append(int(row[1]))
            except (TypeError, ValueError):
                continue
    return max(existing, default=0) + 1


def _decode_saved_orders(value: str) -> Tuple[SavedOrder, ...]:
    try:
        rows = json.loads(value)
        return tuple(
            SavedOrder(
                date=row[0],
                order_no=int(row[1]),
                dn_line=row[2],
                items=row[3],
                link=row[4],
                nominal=row[5] or None,
                arrived_date=row[6],
                status=row[7],
                box=row[8],
            )
            for row in rows
        )
    except (IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SheetRepositoryError("Stored submission record is invalid") from exc
