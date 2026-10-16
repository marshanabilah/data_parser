# Spec: Telegram Order Link Intake, Version 1

## Objective

Replace the existing general sales-entry behavior with an order intake workflow.
An allowlisted Telegram user submits one or more public HTTPS product links and
the DN line associated with each link. The bot extracts product names and, when
available, displayed prices from the linked pages, shows a complete preview, and
writes one row per link to a fixed Google Sheets worksheet after confirmation.

The workflow is intended for small-scale order tracking. Google Sheets remains
the system of record. The bot creates orders only; users maintain fulfillment
fields directly in the sheet after creation.

### Worksheet schema

The fixed worksheet is named `Orders` and uses this exact column order:

| Date | Order No. | DN Line | Items | Link | Nominal | Arrived Date | Status | Box |
|---|---:|---|---|---|---|---|---|---|

Field behavior:

| Field | Source and rules |
|---|---|
| `Date` | Confirmation time in WIB, formatted `YYYY-MM-DD HH:mm` |
| `Order No.` | Permanent, monotonically increasing integer assigned when saving |
| `DN Line` | Required user input; each link must resolve to exactly one DN line |
| `Items` | User override when supplied; otherwise extracted from the public page |
| `Link` | Required public HTTPS product URL supplied by the user |
| `Nominal` | User override when supplied; otherwise the displayed page price and original currency; blank when unavailable |
| `Arrived Date` | Blank at creation; maintained manually in Google Sheets |
| `Status` | `Ordered` at creation; maintained manually in Google Sheets |
| `Box` | Blank at creation; maintained manually in Google Sheets |

### Telegram submission behavior

- A message may contain multiple links and multiple DN lines.
- Each link creates one row.
- Input format is flexible rather than a rigid command syntax.
- An explicitly paired DN and link stay together.
- A DN heading applies to following links until another DN appears.
- If the message contains equal counts of otherwise unpaired DNs and links, they
  are paired by order.
- One DN may apply to several links in the same submission.
- `Items:` and `Nominal:` may be supplied as per-link manual overrides.
- A user-provided override is authoritative and is not replaced by scraped data.
- If any link cannot be mapped to exactly one DN line, the submission cannot be
  confirmed. The bot identifies the unresolved entries and asks the user to
  cancel and resubmit more clearly.

Examples that must be accepted:

```text
DN Line: DN-001
https://example.com/product/one
https://example.com/product/two
```

```text
DN Line: DN-001
https://example.com/product/one

DN Line: DN-002
https://example.com/product/two
Nominal: ¥4,500
Items: Limited Edition Album
```

### Link extraction

For links without overrides, extraction uses this precedence:

1. Structured product data, including JSON-LD `Product.name` and offers
2. Open Graph product/title/price metadata
3. The page's primary product heading or HTML title
4. AI-assisted extraction from bounded, sanitized page text

The bot never invents an item name or price. If an item name cannot be extracted
and no `Items:` override exists, confirmation is blocked. If a price cannot be
extracted and no `Nominal:` override exists, `Nominal` remains blank and the
preview labels it as missing.

The original displayed currency and formatting are preserved as text. Version 1
does not convert currencies or infer exchange rates. A user-supplied nominal is
treated as the total value for that row.

### Preview and confirmation

- The preview displays every proposed row and every worksheet field.
- The only actions are `Confirm` and `Cancel`.
- Corrections require cancellation and resubmission; inline row editing is out of
  scope for version 1.
- The bot checks the `Orders` worksheet for previously stored links. Duplicate
  links remain valid but receive a visible warning in the preview.
- Confirming saves the complete validated batch. A validation failure writes no
  rows.
- Order numbers are reserved at confirmation so concurrent submissions cannot
  receive duplicate IDs. Numbers do not change when rows are sorted or inserted
  and are not reused after deletion.

### Access and URL safety

- Telegram messages, commands, and callback buttons remain restricted by
  `ALLOWED_TELEGRAM_USER_IDS`.
- Only `https://` links are accepted.
- Requests to localhost, private, loopback, link-local, reserved, multicast, and
  other non-public addresses are rejected.
- The destination is checked again after every redirect.
- Fetches use no user credentials, cookies, or authenticated browser session.
- Fetches have bounded time, redirect count, and response size.
- Page content is untrusted input. It cannot issue bot commands, change system
  instructions, select a worksheet, or bypass field validation.

Version 1 supports ordinary server-rendered public pages. Pages that require
login, browser automation, JavaScript rendering, CAPTCHA completion, or bot
challenge bypass are unsupported and require manual `Items:` and `Nominal:`
overrides.

## Tech Stack

- Python 3.12 container runtime
- `python-telegram-bot==21.6` for Telegram updates and callbacks
- `starlette==0.41.2` and `uvicorn==0.32.0` for the Cloud Run webhook service
- `httpx==0.27.2` for bounded HTTPS page requests
- `anthropic==0.34.0` for flexible message interpretation and bounded fallback
  extraction
- `gspread==6.1.2` and `google-auth==2.34.0` for Google Sheets access
- Google Cloud Run with minimum instances `0`, maximum instances `1`
- Google Sheets worksheet `Orders` plus hidden `_BotConfig` state

An HTML parsing library and test tooling may be added during implementation after
this spec is approved. Exact package choices belong in the implementation plan.

## Commands

Current local setup and development:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python bot.py
```

Target verification commands after implementation:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q bot.py sheets.py
docker build -t telegram-order-bot:local .
```

Cloud Run deployment:

```bash
gcloud run deploy telegram-order-bot \
  --source . \
  --region asia-southeast2 \
  --allow-unauthenticated \
  --min 0 \
  --max 1 \
  --env-vars-file .env
```

## Project Structure

Target structure:

```text
bot.py                 Telegram handlers, access control, preview callbacks
order_parser.py        Flexible message parsing and DN/link association
link_extractor.py      Safe public URL fetching and product metadata extraction
sheets.py              Orders worksheet, sequence state, duplicate lookup, writes
models.py              Validated order draft and saved-row data contracts
tests/                 Unit and integration tests with mocked external services
Dockerfile             Cloud Run container
requirements.txt       Runtime and verification dependencies
README.md              Setup, deployment, and user-facing message examples
SPEC.md                Approved product and engineering contract
```

File boundaries may be refined in the approved implementation plan, but link
fetching, message interpretation, sheet persistence, and Telegram presentation
must remain independently testable.

## Code Style

Use typed, small functions with explicit validation and no hidden fallback
values. External data becomes a validated model before reaching Google Sheets.

```python
@dataclass(frozen=True)
class OrderDraft:
    dn_line: str
    link: str
    items: str
    nominal: str | None


def validate_order(draft: OrderDraft) -> None:
    if not draft.dn_line.strip():
        raise OrderValidationError("DN Line is required")
    if not draft.items.strip():
        raise OrderValidationError("Items could not be determined")
```

Conventions:

- `snake_case` for functions and variables; `PascalCase` for data types
- Type annotations on public functions and cross-module data
- Structured logging without message bodies, page contents, credentials, or
  customer order details
- User-facing errors explain the corrective action without exposing stack traces
- Constants for worksheet names, headers, limits, time zone, and default status

## Testing Strategy

Use `pytest` with network and Google Sheets integrations mocked by default.

### Unit tests

- Flexible DN/link pairing, including shared DN, per-link DN, ordered pairing,
  missing DN, and ambiguous associations
- Manual `Items:` and `Nominal:` override precedence
- Metadata precedence across JSON-LD, Open Graph, headings, and title
- Original currency preservation and missing-price behavior
- URL validation for schemes, public IPs, DNS results, and redirects
- Response timeout, redirect, and size limits
- Stable sequential order-number allocation
- Duplicate-link detection and warning generation
- Preview construction and required-field validation

### Integration tests

- Telegram update to parsed preview using fixture HTML and a fake worksheet
- Confirmed multi-link batch creates one correctly ordered row per link
- Cancel writes no rows
- Any unresolved mandatory field writes no rows
- Unauthorized users cannot fetch links, preview orders, or write rows
- Concurrent confirmations cannot receive duplicate order numbers

No automated test may call live merchant pages, Telegram, Anthropic, or Google
Sheets. A documented manual smoke test may use controlled public pages and a test
spreadsheet before deployment.

## Boundaries

### Always do

- Validate every DN/link association before fetching or saving.
- Revalidate every URL destination after redirects.
- Prefer explicit user overrides over extracted values.
- Show all rows and missing optional values before confirmation.
- Preserve original currencies without conversion.
- Allocate permanent unique order numbers at confirmation.
- Keep batch writes all-or-nothing from the user's perspective.
- Run tests, lint, compilation, and secret checks before committing.

### Ask first

- Change the worksheet name, headers, field order, or default status.
- Add authenticated scraping, browser automation, proxies, or CAPTCHA handling.
- Add automatic updates to existing orders.
- Add currency conversion or arithmetic using extracted prices.
- Add a database or replace Google Sheets as the system of record.
- Change the Telegram access-control model or Cloud Run deployment topology.
- Add runtime dependencies not approved through the implementation plan.

### Never do

- Commit tokens, API keys, service-account credentials, `.env` files, or scraped
  page dumps.
- Fetch non-HTTPS or non-public network destinations.
- Send cookies, credentials, or authorization headers to submitted links.
- Treat page instructions as trusted prompts or executable commands.
- Invent item names, prices, currencies, DN lines, or fulfillment values.
- Convert currencies automatically in version 1.
- Edit `Arrived Date`, `Status`, or `Box` after row creation.
- Bypass merchant access controls or anti-bot challenges.

## Success Criteria

1. An allowlisted user can submit one or more public HTTPS product links in one
   flexible-format Telegram message.
2. Every link is mapped to exactly one mandatory DN line or is reported as
   unresolved before confirmation.
3. One preview row and, after confirmation, one sheet row are produced per link.
4. Manual `Items:` and `Nominal:` values override scraped values for their link.
5. Without overrides, item names follow the documented extraction precedence;
   missing item names block confirmation and missing prices remain blank.
6. Nominal values preserve the original displayed currency and are never
   converted or guessed.
7. Confirmed rows use the exact nine-column schema and default values in this
   spec.
8. `Date` records confirmation time in WIB as `YYYY-MM-DD HH:mm`.
9. Order numbers are permanent, unique, monotonically increasing integers and
   remain unchanged after sheet sorting or row insertion.
10. A confirmed multi-link submission writes the full batch without partial
    validation writes.
11. Previously stored links receive a warning but can still be confirmed as new
    orders.
12. Unauthorized Telegram users cannot trigger page fetches or sheet writes.
13. Non-public destinations and unsafe redirects are rejected without making an
    application-level request to them.
14. Unsupported or blocked pages can still be submitted when the user supplies
    the mandatory manual item override; nominal may remain blank.
15. Version 1 writes only to `Orders`; tab-switching commands are removed or
    disabled.
16. `Arrived Date`, later status changes, and `Box` remain manual sheet tasks.
17. Automated tests cover the parsing, extraction, validation, security, sheet,
    and confirmation behaviors without contacting live external services.

## Out of Scope for Version 1

- Editing existing orders through Telegram
- Inline editing inside the confirmation preview
- Automatic shipment, arrival, status, or box updates
- Currency conversion or exchange-rate lookup
- Authenticated pages, browser automation, or anti-bot bypass
- A merchant-specific guarantee for every public product website
- Multiple order worksheets or user-controlled tab switching
- Reporting, analytics, notifications, and order search

## Open Questions

None. Any change to the assumptions or scope above requires updating and
re-approving this spec before implementation.
