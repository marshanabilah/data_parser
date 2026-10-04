# 📊 Sales Telegram Bot

Send sales messages through Telegram, review the parsed result, and save it to
Google Sheets. The bot uses Claude to turn free-form text into structured data
and runs as a webhook service on Google Cloud Run.

## Architecture

```text
Telegram Bot API
       │ HTTPS webhook
       ▼
Google Cloud Run (scales to zero)
       ├── Claude API — text parsing
       └── Google Sheets API — data storage
```

Cloud Run does not need to keep an instance running while the bot is idle. For
light usage, this workload will typically remain within the Cloud Run free tier.
Claude API usage is billed separately.

## Features

- Parses sales messages in Indonesian or English
- Shows a preview with confirmation buttons before saving
- Records timestamps in the WIB time zone
- Switches spreadsheet tabs using `/settab`
- Stores the active-tab setting in a hidden `_BotConfig` worksheet
- Restricts the bot to an allowlist of Telegram user IDs
- Protects the webhook using Telegram's secret-token header

## 1. Create a Telegram bot

1. Open Telegram and find **@BotFather**.
2. Send `/newbot` and follow the instructions.
3. Save the bot token that BotFather provides.

## 2. Prepare Google Sheets

1. Create a Google Sheet and copy the spreadsheet ID from its URL.
2. In Google Cloud Console, enable the Google Sheets API and Google Drive API.
3. Create a service account and download its JSON key.
4. Share the spreadsheet with the service account's `client_email` and grant
   Editor access.

The bot creates the `Sales` tab and hidden `_BotConfig` worksheet when needed.

## 3. Configure the bot

The application uses these environment variables:

| Variable | Required | Description |
|---|---:|---|
| `TELEGRAM_TOKEN` | Yes | Token provided by BotFather |
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `SPREADSHEET_ID` | Yes | Google Spreadsheet ID |
| `GOOGLE_CREDENTIALS_JSON` | Yes | Complete service-account JSON document |
| `TELEGRAM_WEBHOOK_SECRET` | Yes | Random secret used to verify Telegram webhook requests |
| `ALLOWED_TELEGRAM_USER_IDS` | Yes | Comma-separated Telegram user IDs allowed to use the bot |
| `WEBHOOK_BASE_URL` | After first deployment | Cloud Run service URL without a trailing slash |
| `WEBHOOK_PATH` | No | Webhook path; defaults to `telegram` |
| `PORT` | Automatic | Supplied by Cloud Run; defaults locally to `8080` |

Variable names and placeholder values are available in `.env.example`. Never
commit real credentials or API keys.

Create a webhook secret with:

```bash
openssl rand -hex 32
```

### Configure access control

`ALLOWED_TELEGRAM_USER_IDS` is a fail-closed allowlist. The application refuses
to start if the list is missing or empty. Multiple users can be separated with
commas:

```dotenv
ALLOWED_TELEGRAM_USER_IDS=123456789,987654321
```

To discover your user ID safely:

1. Temporarily set the allowlist to a known numeric placeholder, such as `0`.
2. Deploy the bot and send `/start` to it.
3. The access-denied reply displays your own Telegram user ID.
4. Replace the placeholder with that ID and deploy again.

The allowlist uses **user IDs**, not usernames or chat IDs. It protects normal
messages, commands, and confirmation-button callbacks.

## 4. Deploy to Google Cloud Run

The first deployment does not require `WEBHOOK_BASE_URL`. The service starts its
HTTP server but does not register a Telegram webhook until that URL is set.

1. Create a Google Cloud project and enable billing, the Cloud Run API, Cloud
   Build API, and Artifact Registry API.
2. Copy `.env.example` to `.env`, fill in every required variable, and leave
   `WEBHOOK_BASE_URL` empty for the first deployment.
3. From the repository directory, deploy the source:

   ```bash
   gcloud run deploy sales-telegram-bot \
     --source . \
     --region asia-southeast2 \
     --allow-unauthenticated \
     --min 0 \
     --max 1 \
     --env-vars-file .env
   ```

4. Copy the displayed service URL, such as
   `https://sales-telegram-bot-xxxxx.asia-southeast2.run.app`, and save it as
   `WEBHOOK_BASE_URL` in `.env`.
5. Run the same deployment command again. When the new revision starts, the bot
   automatically registers its webhook with Telegram.

For production, store the Telegram token, Anthropic key, webhook secret, and
service-account JSON in Google Secret Manager, then expose those secrets to the
Cloud Run container as environment variables.

### Verify the deployment

The root endpoint should return `{"status":"ok"}`:

```bash
curl https://YOUR-SERVICE-URL.run.app/
```

Then send a sales message to the Telegram bot:

```text
bakso ayam 10 porsi, es teh 20 gelas - Andi
```

## Usage

| Command | Purpose |
|---|---|
| `/start` | Show the welcome message and command list |
| `/help` | Show usage help |
| `/settab <name>` | Select the active tab; it is created when data is first saved |
| `/currenttab` | Show the active tab |
| `/listtabs` | List all data tabs |

Example messages:

```text
kaos polos 5 pcs sama celana jeans 3 - Andi, dari uniqlo
vitamin c 10, sabun muka 5 - Siti
beras 5kg, minyak goreng 3 botol - Budi
```

## Google Sheets columns

| Date | Name | Store | Item Name | Quantity |
|---|---|---|---|---:|
| 2026-05-24 14:30 | Andi | Supermarket | Bakso Ayam | 10 |

## Run locally

A local webhook requires a public HTTPS URL, usually provided through a tunnel.
Install the dependencies, export the variables from `.env.example`, and run:

```bash
python -m pip install -r requirements.txt
python bot.py
```

Set `WEBHOOK_BASE_URL` to the public HTTPS tunnel URL. The health endpoint uses
port `8080` by default.

## Project structure

```text
.
├── bot.py
├── sheets.py
├── requirements.txt
├── Dockerfile
├── .dockerignore
├── .env.example
└── README.md
```

## Troubleshooting

- **Container fails to start:** verify that all required variables except
  `WEBHOOK_BASE_URL` are present and that the service-account JSON is valid.
- **`ALLOWED_TELEGRAM_USER_IDS` error:** use numeric user IDs separated by commas;
  do not use `@usernames`.
- **Bot does not respond:** verify that `WEBHOOK_BASE_URL` exactly matches the
  Cloud Run service URL and that the latest revision deployed successfully.
- **Google Sheets fails:** verify that the spreadsheet was shared with the
  service account using Editor access.
- **Webhook returns 403:** if `TELEGRAM_WEBHOOK_SECRET` changed, deploy a new
  revision so the bot can register the updated secret with Telegram.
