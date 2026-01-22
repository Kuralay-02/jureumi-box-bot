import os
import json
import asyncio
from datetime import datetime

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

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# ================== GOOGLE SHEETS ==================

creds_dict = json.loads(GOOGLE_CREDENTIALS)
creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
gc = gspread.authorize(creds)

registry_sheet = gc.open_by_url(REGISTRY_SHEET_URL).sheet1

# ================== ХРАНЕНИЕ ==================

users = set()
known_boxes = set()
awaiting_username = set()

# ================== КОМАНДЫ ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    users.add(chat_id)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]]
    )

    await update.message.reply_text(
        "Здравствуйте!\n\n"
        "Я уведомляю о новых коробках и считаю сумму к оплате 💸",
        reply_markup=keyboard,
    )

# ================== КНОПКИ ==================

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "calc":
        awaiting_username.add(query.message.chat.id)
        await query.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n"
            "(например: @anna)"
        )

# ================== ВВОД ЮЗЕРНЕЙМА ==================

async def handle_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    if chat_id not in awaiting_username:
        return

    if not text.startswith("@"):
        await update.message.reply_text(
            "Юзернейм должен начинаться с @\n"
            "Например: @anna"
        )
        return

    awaiting_username.remove(chat_id)
    username = text.lower()

    await calculate_sum(update, username)

# ================== ПОДСЧЁТ СУММЫ ==================

async def calculate_sum(update: Update, username: str):
    total = 0
    boxes_found = []

    rows = registry_sheet.get_all_records()

    for row in rows:
        if str(row.get("Активна")).lower() != "да":
            continue

        sheet_url = row.get("Ссылка на таблицу")
        box_name = row.get("Название коробки")

        try:
            sheet = gc.open_by_url(sheet_url).sheet1
            data = sheet.get_all_records()
        except Exception:
            continue

        for item in data:
            user = str(item.get("username", "")).lower()
            price = item.get("sum")

            if user == username:
                try:
                    total += float(price)
                    boxes_found.append(box_name)
                except Exception:
                    pass

    if total == 0:
        await update.message.reply_text(
            f"По юзернейму {username} я ничего не нашла 🫶"
        )
        return

    text = (
        f"📦 Найдено в коробках:\n"
        f"{', '.join(set(boxes_found))}\n\n"
        f"💰 *Итого к оплате:* **{int(total)}**"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
    )

# ================== УВЕДОМЛЕНИЯ О НОВЫХ КОРОБКАХ ==================

async def notify_new_boxes(app):
    while True:
        rows = registry_sheet.get_all_records()

        for row in rows:
            if str(row.get("Активна")).lower() != "да":
                continue

            box_name = row.get("Название коробки")
            link = row.get("Ссылка на таблицу")
            deadline = row.get("Дедлайн оплаты")

            key = f"{box_name}|{link}"
            if key in known_boxes:
                continue

            known_boxes.add(key)

            text = (
                "📦 *Вышла новая коробка!*\n"
                "Проверь себя по юзернейму или я могу посчитать за тебя ❤️\n\n"
                f"*{box_name}*\n{link}\n\n"
                f"⏰ Дедлайн: {deadline}"
            )

            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]]
            )

            for chat_id in users:
                try:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

        await asyncio.sleep(30)

# ================== ЗАПУСК ==================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user))

    app.job_queue.run_repeating(
        notify_new_boxes,
        interval=60,
        first=5
    )

    app.run_polling()


if __name__ == "__main__":
    main()

