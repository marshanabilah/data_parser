# Telegram Order Link Bot

This bot turns Telegram messages containing public product links into confirmed
order rows in Google Sheets. It extracts product names and displayed prices,
shows every proposed field for review, and writes only after an allowlisted user
presses **Confirm**.

It runs as a webhook service on Google Cloud Run. With minimum instances set to
zero, it can scale down while idle; Anthropic API usage and any Google Cloud
usage outside the free allowance are billed separately.

## What it does

- Accepts multiple public HTTPS product links in one message
- Creates one order row for every submitted link occurrence
- Supports one shared DN Line or a separate DN Line per link
- Uses manual `Items:` and `Nominal:` values when supplied
- Extracts missing item names and displayed prices from ordinary public HTML
- Preserves the displayed currency text without conversion
- Warns about links already present in `Orders` without blocking them
- Uses permanent sequential order numbers and WIB confirmation timestamps
- Restricts all Telegram actions to an explicit user-ID allowlist
- Verifies Telegram's webhook secret and rejects private-network destinations

Pages requiring login, JavaScript rendering, CAPTCHA completion, or anti-bot
bypass are unsupported. Supply `Items:` for such a page. `Nominal:` may be left
blank.

## Message format

A DN Line is mandatory. Formatting can be flexible because the bot validates
the interpreted mapping and displays a complete preview before saving.

One DN shared by multiple links:

```text
DN Line: DN-001
https://example.com/product/one
https://example.com/product/two
```

Separate DNs paired with their links:

```text
DN Line: DN-001
https://example.com/product/one

DN Line: DN-002
https://example.com/product/two
```

Manual fields for one link:

```text
DN Line: DN-002
https://example.com/product/two
Items: Limited Edition Album
Nominal: ¥4,500
```

`Items:` overrides the extracted name. `Nominal:` overrides the extracted
price and is treated as the complete value for that row. Corrections are made
by cancelling the preview and resubmitting the message.

The only commands are:

| Command | Purpose |
|---|---|
| `/start` | Show the welcome message and examples |
| `/help` | Show usage instructions |

## Google Sheets layout

The bot creates or validates one fixed worksheet named `Orders` with these exact
columns:

| Date | Order No. | DN Line | Items | Link | Nominal | Arrived Date | Status | Box |
|---|---:|---|---|---|---|---|---|---|
| 2026-10-16 14:30 | 1 | DN-001 | Limited Edition Album | https://example.com/product/one | ¥4,500 |  | Ordered |  |

- `Date` is assigned on confirmation in WIB using `YYYY-MM-DD HH:mm`.
- `Order No.` is a stable, increasing ID; deleted IDs are not reused.
- `Status` starts as `Ordered`.
- `Arrived Date` and `Box` start blank.
- Fulfilment changes to `Arrived Date`, `Status`, and `Box` are manual Sheet
  edits in version 1.
- The hidden `_BotConfig` worksheet stores sequence and idempotency state.
- Existing unrelated worksheets are not modified.

## Setup

### 1. Create the Telegram bot

Open **@BotFather** in Telegram, send `/newbot`, and keep the returned token.

### 2. Prepare Google Sheets

1. Create a spreadsheet and copy its ID from the URL.
2. Enable the Google Sheets API and Google Drive API in Google Cloud.
3. Create a service account and obtain its JSON credentials.
4. Share the spreadsheet with the credentials' `client_email` as an Editor.

The bot creates `Orders` and hidden `_BotConfig` when first used. If an existing
`Orders` worksheet has different headers, the bot fails safely instead of
rewriting it.

### 3. Configure environment variables

Copy `.env.example` to a local untracked `.env` and replace every placeholder.

| Variable | Required | Purpose |
|---|---:|---|
| `TELEGRAM_TOKEN` | Yes | Token from BotFather |
| `ANTHROPIC_API_KEY` | Yes | Flexible DN mapping and final name fallback |
| `SPREADSHEET_ID` | Yes | Target Google Spreadsheet ID |
| `GOOGLE_CREDENTIALS_JSON` | Yes | Complete service-account JSON document |
| `TELEGRAM_WEBHOOK_SECRET` | Yes | Verifies incoming Telegram webhook calls |
| `ALLOWED_TELEGRAM_USER_IDS` | Yes | Comma-separated Telegram user IDs |
| `WEBHOOK_BASE_URL` | After first deploy | Cloud Run URL without a trailing slash |
| `WEBHOOK_PATH` | No | Defaults to `telegram` |
| `PORT` | No | Supplied by Cloud Run; locally defaults to `8080` |

Generate a webhook secret locally:

```bash
openssl rand -hex 32
```

The allowlist is fail-closed and uses numeric user IDs, not usernames or chat
IDs:

```dotenv
ALLOWED_TELEGRAM_USER_IDS=123456789,987654321
```

If you do not know your user ID, temporarily use `0`, deploy, and send `/start`.
The access-denied response displays the sender's ID. Replace `0` immediately and
deploy again.

## Run locally

Install dependencies in a virtual environment, export the variables, and start
the service:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python bot.py
```

Telegram webhooks require a public HTTPS URL, so local Telegram testing also
requires a tunnel and `WEBHOOK_BASE_URL` set to its public origin. The local
health endpoint is `http://localhost:8080/`.

For development checks:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

## Deploy to Google Cloud Run

Enable Cloud Run, Cloud Build, Artifact Registry, and billing in the selected
Google Cloud project. The first deployment may leave `WEBHOOK_BASE_URL` blank:

```bash
gcloud run deploy telegram-order-bot \
  --source . \
  --region asia-southeast2 \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 1 \
  --env-vars-file .env
```

Copy the resulting service URL into `WEBHOOK_BASE_URL`, then run the same command
again. The new revision registers the Telegram webhook during startup.

Keep `--max-instances 1` and `--concurrency 1`: version 1 serializes sequential
order-number allocation in one running instance. Deploy during a quiet window
because old and new Cloud Run revisions can briefly overlap. Use Google Secret
Manager for production tokens and credentials.

Verify the health endpoint:

```bash
curl https://YOUR-SERVICE-URL.run.app/
```

It should return `{"status":"ok"}`.

## Manual smoke test

Use a separate test spreadsheet:

1. Send one shared DN with two controlled public product links.
2. Confirm the preview shows two rows and all nine fields.
3. Confirm and verify sequential IDs, WIB dates, `Ordered`, and blank fulfilment
   fields in `Orders`.
4. Resubmit one link and verify the duplicate warning appears.
5. Test a blocked page with a manual `Items:` value and confirm that a missing
   nominal remains visibly blank.
6. Send `/start` from a non-allowlisted account and verify no fetch or write
   occurs.
7. Cancel a preview and verify that no row was added.

Deployment and live-data changes are intentionally separate from the automated
test suite.

## Project files

```text
bot.py                 Telegram and webhook presentation
models.py              Validated immutable data contracts
order_parser.py        Link extraction and DN/override mapping
link_extractor.py      Bounded public-page fetching and metadata extraction
order_service.py       Order preparation and confirmation orchestration
sheets.py              Fixed Orders worksheet persistence
tests/                  Offline automated tests
SPEC.md                 Approved product and engineering requirements
tasks/                  Implementation plan and checklist
```

## Troubleshooting

- **Container does not start:** check all required variables and credential JSON.
- **Access denied:** add the numeric Telegram user ID to
  `ALLOWED_TELEGRAM_USER_IDS`.
- **Page cannot be read:** resubmit with an explicit `Items:` value; `Nominal:`
  is optional.
- **Worksheet schema error:** make the `Orders` header exactly match the table
  above, or use a fresh test spreadsheet.
- **Webhook returns 403:** confirm the deployed webhook secret matches the value
  registered by the latest revision.
- **Bot does not respond:** verify `WEBHOOK_BASE_URL`, the latest revision logs,
  and the `/` health endpoint.
