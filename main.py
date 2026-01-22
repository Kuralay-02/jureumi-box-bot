import os
import json
import gspread
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from oauth2client.service_account import ServiceAccountCredentials

BOT_TOKEN = os.getenv("BOT_TOKEN")
REGISTRY_SHEET_URL = os.getenv("REGISTRY_SHEET_URL")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

# ---------------- Google auth ----------------
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

# ---------------- START ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
    ])
    await update.message.reply_text(
        "Здравствуйте!\n\nЯ помогу посчитать сумму к оплате.\nНажмите кнопку ниже 👇",
        reply_markup=keyboard
    )

# ---------------- BUTTON ----------------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "calc":
        context.user_data["waiting_username"] = True
        await query.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n(например: @anna)"
        )

# ---------------- USERNAME ----------------
async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_username"):
        return

    username = update.message.text.strip().lower()
    if not username.startswith("@"):
        username = "@" + username

    context.user_data["waiting_username"] = False

    try:
        sheet = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
        rows = sheet.get_all_records()
        await update.message.reply_text(
    "DEBUG заголовки колонок:\n"
    + ", ".join(rows[0].keys())
)

    except Exception:
        await update.message.reply_text("Ошибка доступа к таблице 😢")
        return

    user_rows = [
        r for r in rows
        if str(r.get("Ник в тг", "")).strip().lower() == username
    ]

    if not user_rows:
        await update.message.reply_text(
            f"По юзернейму {username} я ничего не нашла 🤍"
        )
        return

    total_kzt = 0
    total_rub = 0
    text_lines = []

    for r in user_rows:
        num = r.get("Номер разбора", "")
        name = r.get("Название позиции", "")
        kzt = int(r.get("Цена в тенге", 0) or 0)
        rub = int(r.get("Цена в рублях", 0) or 0)

        total_kzt += kzt
        total_rub += rub

        text_lines.append(
            f"• {num} — {name}: {kzt}₸ / {rub}₽"
        )

    text = (
        f"📦 Ваши позиции:\n\n"
        + "\n".join(text_lines)
        + f"\n\n💰 Итого: {total_kzt}₸ / {total_rub}₽"
    )

    await update.message.reply_text(text)


# ---------------- MAIN ----------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    app.run_polling()

if __name__ == "__main__":
    main()
