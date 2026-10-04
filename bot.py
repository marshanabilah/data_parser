import os
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, TypeHandler, ApplicationHandlerStop,
    filters, ContextTypes
)
import anthropic
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from sheets import append_sales_rows, set_active_tab, get_active_tab, list_tabs

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL", "").rstrip("/")
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "telegram")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
ALLOWED_TELEGRAM_USER_IDS_RAW = os.environ.get("ALLOWED_TELEGRAM_USER_IDS", "")
PORT = int(os.environ.get("PORT", "8080"))

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def parse_allowed_user_ids(raw_value: str) -> frozenset[int]:
    """Parse a comma-separated Telegram user ID allowlist."""
    values = [value.strip() for value in raw_value.split(",") if value.strip()]
    try:
        return frozenset(int(value) for value in values)
    except ValueError as exc:
        raise RuntimeError(
            "ALLOWED_TELEGRAM_USER_IDS must contain comma-separated integers"
        ) from exc


ALLOWED_TELEGRAM_USER_IDS = parse_allowed_user_ids(ALLOWED_TELEGRAM_USER_IDS_RAW)

SYSTEM_PROMPT = """You are a sales data parser for a retail business with multiple store types.
The user will send you a message describing their daily sales.
Your job is to extract the sales data and return it as a JSON array.

Each item in the array should have:
- "item_name": name of the item sold (string)
- "quantity": number of units sold (integer)
- "name": salesperson or customer name if mentioned (string, or "Unknown" if not mentioned)
- "store": the store or store type where the item was sold (string)

How to determine "store":
- If the user explicitly mentions a store name, use it as-is (e.g., "Uniqlo", "GU", "Guardian").
- If no store is mentioned, infer the most likely store type from the item name:
  - Fashion/clothing items (e.g., t-shirt, jeans, dress, jacket) → "Uniqlo" or "GU" depending on context clues (GU tends to be trendier/cheaper, Uniqlo more basic/quality)
  - Health, beauty, medicine, skincare, vitamins → "Drugstore"
  - Groceries, food, drinks, household items, snacks → "Supermarket"
  - If truly unclear, use "Unknown Store"

Rules:
- Always return ONLY valid JSON, no explanation, no markdown, no backticks.
- If multiple items are mentioned, return multiple objects in the array.
- Handle Indonesian language naturally (e.g., "porsi", "gelas", "buah", "pcs" are all quantity units).
- If quantity is ambiguous, default to 1.
- Strip units from quantity (e.g., "10 porsi" → quantity: 10).
- All items in one message can share the same store unless different stores are specified.

Example input: "kaos polos 5 pcs sama celana jeans 3 - Andi, dari uniqlo"
Example output:
[
  {"item_name": "Kaos Polos", "quantity": 5, "name": "Andi", "store": "Uniqlo"},
  {"item_name": "Celana Jeans", "quantity": 3, "name": "Andi", "store": "Uniqlo"}
]

Example input: "vitamin c 10, sabun muka 5 - Siti"
Example output:
[
  {"item_name": "Vitamin C", "quantity": 10, "name": "Siti", "store": "Drugstore"},
  {"item_name": "Sabun Muka", "quantity": 5, "name": "Siti", "store": "Drugstore"}
]

Example input: "beras 5kg, minyak goreng 3 botol, deterjen 2 - Budi"
Example output:
[
  {"item_name": "Beras", "quantity": 5, "name": "Budi", "store": "Supermarket"},
  {"item_name": "Minyak Goreng", "quantity": 3, "name": "Budi", "store": "Supermarket"},
  {"item_name": "Deterjen", "quantity": 2, "name": "Budi", "store": "Supermarket"}
]
"""


def parse_sales_message(message: str) -> list[dict]:
    """Use Claude to parse the sales message into structured data."""
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": message}]
    )
    raw = response.content[0].text.strip()
    logger.info(f"Claude raw response: {repr(raw)}")

    # Strip markdown code fences if present (e.g. ```json ... ```)
    import re
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    data = json.loads(raw)
    return data


def build_preview(rows: list[dict]) -> str:
    """Build a preview message showing parsed rows before confirmation."""
    lines = ["📋 *Preview data penjualan:*\n"]
    for i, row in enumerate(rows, 1):
        lines.append(
            f"{i}. 🏪 *{row['store']}* | 📦 {row['item_name']} "
            f"— {row['quantity']} pcs — 👤 {row['name']}"
        )
    lines.append(f"\n🕐 {rows[0]['date']}")
    lines.append("\n*Apakah data sudah benar?*")
    return "\n".join(lines)


COMMANDS_TEXT = """
📋 *Daftar perintah:*

*📊 Tab Management*
`/settab <nama>` — ganti tab aktif (buat baru otomatis jika belum ada)
`/currenttab` — lihat tab yang sedang aktif
`/listtabs` — lihat semua tab di spreadsheet

*ℹ️ Lainnya*
`/start` — pesan sambutan & daftar perintah
`/help` — cara penggunaan & daftar perintah

*💬 Input Penjualan*
Kirim pesan biasa (bukan command) untuk mencatat penjualan, contoh:
`bakso ayam 10 porsi, es teh 20 gelas - Andi`
`kaos polos 5 pcs - Budi, dari uniqlo`
Bot akan tampilkan preview dulu sebelum menyimpan ke sheet.
"""


async def enforce_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop updates from Telegram users who are not on the allowlist."""
    del context
    user = update.effective_user
    if user and user.id in ALLOWED_TELEGRAM_USER_IDS:
        return

    user_id = user.id if user else "unknown"
    logger.warning("Rejected Telegram update from unauthorized user ID %s", user_id)

    if update.callback_query:
        await update.callback_query.answer(
            f"Access denied. Your Telegram user ID is {user_id}.",
            show_alert=True,
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            "⛔ Access denied.\n\n"
            f"Your Telegram user ID is `{user_id}`. Ask the bot administrator "
            "to add it to `ALLOWED_TELEGRAM_USER_IDS`.",
            parse_mode="Markdown",
        )

    raise ApplicationHandlerStop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Halo! Selamat datang di Sales Bot!*\n\n"
        "Kirim pesan penjualan kamu dan data akan otomatis masuk ke Google Sheets 📊\n"
        + COMMANDS_TEXT,
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(COMMANDS_TEXT, parse_mode="Markdown")


async def set_tab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/settab <tab_name> — switch to a different tab (creates it if it doesn't exist)"""
    if not context.args:
        await update.message.reply_text(
            "⚠️ *Cara pakai:*\n`/settab <nama tab>`\n\n"
            "Contoh:\n`/settab Juni 2026`\n`/settab Q3 Sales`",
            parse_mode="Markdown"
        )
        return

    tab_name = " ".join(context.args).strip()
    set_active_tab(tab_name)

    existing_tabs = list_tabs()
    if tab_name in existing_tabs:
        msg = f"✅ *Tab aktif diperbarui!*\n\n📋 Menggunakan tab yang sudah ada: *{tab_name}*"
    else:
        msg = f"✅ *Tab aktif diperbarui!*\n\n🆕 Tab baru *{tab_name}* akan dibuat otomatis saat data pertama masuk."

    await update.message.reply_text(msg, parse_mode="Markdown")
    logger.info(f"Active tab set to: {tab_name}")


async def current_tab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/currenttab — show the active tab name"""
    tab_name = get_active_tab()
    await update.message.reply_text(
        f"📋 *Tab aktif saat ini:*\n*{tab_name}*",
        parse_mode="Markdown"
    )


async def list_tabs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/listtabs — show all existing tabs in the spreadsheet"""
    try:
        tabs = list_tabs()
        active = get_active_tab()
        lines = ["📊 *Semua tab di spreadsheet:*\n"]
        for tab in tabs:
            marker = " ← aktif" if tab == active else ""
            lines.append(f"• {tab}{marker}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Gagal mengambil daftar tab: {str(e)}")


async def handle_sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    logger.info(f"Received message: {user_message}")

    processing_msg = await update.message.reply_text("⏳ Memproses data penjualan...")

    try:
        sales_data = parse_sales_message(user_message)

        if not sales_data:
            await processing_msg.edit_text("❌ Tidak ada data penjualan yang terdeteksi. Coba kirim ulang.")
            return

        WIB = timezone(timedelta(hours=7))
        today = datetime.now(WIB).strftime("%Y-%m-%d %H:%M")
        rows = []
        for item in sales_data:
            rows.append({
                "date": today,
                "item_name": item.get("item_name", ""),
                "quantity": item.get("quantity", 0),
                "name": item.get("name", "Unknown"),
                "store": item.get("store", "Unknown Store"),
            })

        # Save rows to user context so we can access them on confirm/cancel
        context.user_data["pending_rows"] = rows

        # Show preview with inline confirm/cancel buttons
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Ya, simpan", callback_data="confirm"),
                InlineKeyboardButton("❌ Batalkan", callback_data="cancel"),
            ]
        ])

        await processing_msg.edit_text(
            build_preview(rows),
            parse_mode="Markdown",
            reply_markup=keyboard
        )

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        await processing_msg.edit_text(
            "❌ Gagal memproses pesan. Coba format seperti:\n`bakso 10 porsi - Andi`",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await processing_msg.edit_text(f"❌ Terjadi kesalahan: {str(e)}")


async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    rows = context.user_data.get("pending_rows")

    if not rows:
        await query.edit_message_text("⚠️ Tidak ada data pending. Kirim pesan penjualan dulu.")
        return

    if query.data == "confirm":
        try:
            append_sales_rows(rows)
            context.user_data.pop("pending_rows", None)

            lines = ["✅ *Data berhasil disimpan ke Google Sheets!*\n"]
            for row in rows:
                lines.append(
                    f"🏪 *{row['store']}* | 📦 {row['item_name']} "
                    f"— {row['quantity']} pcs — 👤 {row['name']}"
                )
            lines.append(f"\n🕐 {rows[0]['date']}")

            await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
            logger.info(f"Successfully logged {len(rows)} rows to Google Sheets.")

        except Exception as e:
            logger.error(f"Error writing to sheets: {e}")
            await query.edit_message_text(f"❌ Gagal menyimpan ke Google Sheets: {str(e)}")

    elif query.data == "cancel":
        context.user_data.pop("pending_rows", None)
        await query.edit_message_text(
            "❌ *Data dibatalkan.*\n\nKirim ulang pesan penjualan jika ingin mencoba lagi.",
            parse_mode="Markdown"
        )


def build_telegram_application():
    """Build the Telegram application and register all bot handlers."""
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Group -1 always runs before the bot's functional handlers. Unauthorized
    # updates are stopped here, including commands and callback buttons.
    app.add_handler(TypeHandler(Update, enforce_access), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settab", set_tab))
    app.add_handler(CommandHandler("currenttab", current_tab))
    app.add_handler(CommandHandler("listtabs", list_tabs_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sales))
    app.add_handler(CallbackQueryHandler(handle_confirmation))
    return app


def build_web_application(telegram_app):
    """Build the HTTP application that receives Telegram webhook updates."""

    @asynccontextmanager
    async def lifespan(_app):
        async with telegram_app:
            await telegram_app.start()
            if WEBHOOK_BASE_URL:
                await telegram_app.bot.set_webhook(
                    url=f"{WEBHOOK_BASE_URL}/{WEBHOOK_PATH}",
                    secret_token=TELEGRAM_WEBHOOK_SECRET,
                    drop_pending_updates=True,
                )
                logger.info("Telegram webhook registered")
            else:
                logger.warning(
                    "WEBHOOK_BASE_URL is not set; HTTP server is ready but "
                    "the Telegram webhook was not registered"
                )
            yield
            await telegram_app.stop()

    async def health(_request: Request):
        return JSONResponse({"status": "ok"})

    async def telegram_webhook(request: Request):
        if TELEGRAM_WEBHOOK_SECRET:
            supplied_secret = request.headers.get(
                "X-Telegram-Bot-Api-Secret-Token", ""
            )
            if not secrets.compare_digest(supplied_secret, TELEGRAM_WEBHOOK_SECRET):
                return Response(status_code=403)

        try:
            update = Update.de_json(await request.json(), telegram_app.bot)
        except (json.JSONDecodeError, ValueError):
            return Response(status_code=400)

        # Finish processing before returning the HTTP response. Cloud Run may
        # suspend CPU after the request ends when min instances is zero.
        await telegram_app.process_update(update)
        return Response(status_code=200)

    return Starlette(
        routes=[
            Route("/", health, methods=["GET"]),
            Route(f"/{WEBHOOK_PATH}", telegram_webhook, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


def main():
    if not TELEGRAM_WEBHOOK_SECRET:
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET is required")
    if not ALLOWED_TELEGRAM_USER_IDS:
        raise RuntimeError("ALLOWED_TELEGRAM_USER_IDS must contain at least one user ID")

    logger.info("Bot HTTP server is starting on port %s", PORT)
    uvicorn.run(
        build_web_application(build_telegram_application()),
        host="0.0.0.0",
        port=PORT,
    )


if __name__ == "__main__":
    main()
