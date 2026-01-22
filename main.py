import os
import json

import gspread
from google.oauth2.service_account import Credentials

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

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
REGISTRY_SHEET_URL = os.getenv("REGISTRY_SHEET_URL")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

# ================== GOOGLE SHEETS ==================

creds_dict = json.loads(GOOGLE_CREDENTIALS)

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)

# ================== /start ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
    ])
    await update.message.reply_text(
        "Здравствуйте!\n\n"
        "Я помогу посчитать сумму к оплате.\n"
        "Нажмите кнопку ниже 👇",
        reply_markup=keyboard
    )

# ================== КНОПКА ==================

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "calc":
        context.user_data["waiting_username"] = True
        await query.message.reply_text(
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
        raw_rows = sheet.get_all_values()[1:]  # без заголовков
    except Exception:
        await update.message.reply_text("Ошибка доступа к таблице 😢")
        return

    user_rows = []
    for r in raw_rows:
        tg_nick = str(r[2]).strip().lower()  # колонка C — Ник в тг
        if tg_nick == username:
            user_rows.append(r)

    if not user_rows:
        await update.message.reply_text(
            f"По юзернейму {username} я ничего не нашла 🤍"
        )
        return

    total_kzt = 0
    total_rub = 0
    lines = []

    for r in user_rows:
        box_num = r[0]
        item_name = r[1]
        price_kzt = int(r[3]) if r[3].isdigit() else 0
        price_rub = int(r[4]) if r[4].isdigit() else 0

        total_kzt += price_kzt
        total_rub += price_rub

        lines.append(
            f"📦 Разбор {box_num}\n"
            f"{item_name}\n"
            f"— {price_kzt} ₸ / {price_rub} ₽"
        )

    text = (
        f"Нашла для {username}:\n\n"
        + "\n\n".join(lines)
        + f"\n\n💰 Итого:\n{total_kzt} ₸ / {total_rub} ₽"
    )

    await update.message.reply_text(text)


# ================== ЗАПУСК ==================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    app.run_polling()

if __name__ == "__main__":
    main()

