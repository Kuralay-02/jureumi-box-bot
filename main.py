import os
import json
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.environ["BOT_TOKEN"]
REGISTRY_SHEET_URL = os.environ["REGISTRY_SHEET_URL"]
GOOGLE_CREDENTIALS = json.loads(os.environ["GOOGLE_CREDENTIALS"])

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ================== GOOGLE ==================

creds = Credentials.from_service_account_info(
    GOOGLE_CREDENTIALS, scopes=SCOPES
)
gc = gspread.authorize(creds)

# ================== КЛАВИАТУРА ==================

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [["📦 Посчитать мою сумму к оплате доставки до админа"]],
    resize_keyboard=True
)

# ================== /start ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здравствуйте!\n\n"
        "Я помогу посчитать сумму к оплате.\n"
        "Нажмите кнопку ниже 👇",
        reply_markup=MAIN_KEYBOARD,
    )
    context.user_data.clear()

# ================== КНОПКА ==================

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "посчитать" in update.message.text.lower():
        context.user_data["waiting_username"] = True
        await update.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n"
            "(например: @anna)"
        )

# ================== ЮЗЕРНЕЙМ ==================

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
    except Exception:
        await update.message.reply_text("Ошибка доступа к таблице 🥲")
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
    lines = []

    for r in user_rows:
        num = r.get("Номер разбора", "")
        name = r.get("Название позиции", "")
        kzt = int(r.get("Цена в тенге", 0) or 0)
        rub = int(r.get("Цена в рублях", 0) or 0)

        total_kzt += kzt
        total_rub += rub

        lines.append(
            f"• #{num} — {name}\n"
            f"  {kzt} ₸ / {rub} ₽"
        )

    text = (
        f"{username}\n\n"
        + "\n\n".join(lines)
        + "\n\n"
        f"💰 *Общая сумма к оплате:*\n"
        f"*{total_kzt} ₸ / {total_rub} ₽*"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD
    )

# ================== ЗАПУСК ==================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    app.run_polling()

if __name__ == "__main__":
    main()
