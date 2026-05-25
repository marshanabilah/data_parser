import os
import json
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]
HEADERS = ["Date", "Name", "Store","Item Name", "Quantity"]

STATE_FILE = "/data/active_tab.json"
DEFAULT_TAB = "Sales"


def _get_gspread_client():
    raw = os.environ["GOOGLE_CREDENTIALS_JSON"]
    # Railway sometimes double-escapes newlines in the private key — fix it
    raw = raw.replace("\\n", "\n")
    creds_dict = json.loads(raw)
    # Also fix the private_key field specifically if it's still escaped
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


# ── Active tab persistence ─────────────────────────────────────────────────

def get_active_tab() -> str:
    """Return the currently active tab name."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("tab_name", DEFAULT_TAB)
    except (FileNotFoundError, json.JSONDecodeError):
        return DEFAULT_TAB


def set_active_tab(tab_name: str):
    """Persist a new tab name to disk."""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"tab_name": tab_name}, f)


def list_tabs() -> list[str]:
    """Return all existing tab names in the spreadsheet."""
    client = _get_gspread_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    return [ws.title for ws in spreadsheet.worksheets()]


# ── Sheet access ───────────────────────────────────────────────────────────

def get_sheet():
    """Return the active worksheet, creating it if it doesn't exist."""
    tab_name = get_active_tab()
    client = _get_gspread_client()
    spreadsheet = client.open_by_key(SPREADSHEET_ID)

    try:
        worksheet = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=10)

    if not worksheet.get_all_values():
        worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")

    return worksheet


def append_sales_rows(rows: list[dict]):
    """Append one or more sales rows to the active tab."""
    worksheet = get_sheet()
    data = [
        [
            row.get("date", ""),
            row.get("name", "Unknown"),
            row.get("store", "Unknown Store"),
            row.get("item_name", ""),
            row.get("quantity", 0),
        ]
        for row in rows
    ]
    worksheet.append_rows(data, value_input_option="USER_ENTERED")