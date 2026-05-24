import os
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import anthropic
from sheets import append_sales_rows, set_active_tab, get_active_tab, list_tabs

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

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
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": message}]
    )
    raw = response.content[0].text.strip()
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

        today = datetime.now().strftime("%Y-%m-%d %H:%M")
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


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settab", set_tab))
    app.add_handler(CommandHandler("currenttab", current_tab))
    app.add_handler(CommandHandler("listtabs", list_tabs_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sales))
    app.add_handler(CallbackQueryHandler(handle_confirmation))

    logger.info("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()