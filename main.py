import os
import json
import re
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
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.environ["BOT_TOKEN"]
REGISTRY_SHEET_URL = os.environ["REGISTRY_SHEET_URL"]
GOOGLE_CREDENTIALS = os.environ["GOOGLE_CREDENTIALS"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDENTIALS),
    scopes=SCOPES,
)
gc = gspread.authorize(creds)

# ================= ХРАНИЛИЩА =================

SUBSCRIBERS = set()

# ================= /start =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    SUBSCRIBERS.add(update.effective_chat.id)

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📦 Посчитать мою сумму к оплате доставки до админа", callback_data="calc")]]
    )

    await update.message.reply_text(
        "Здравствуйте!\n"
        "Я буду уведомлять о выходе новых доставок коробок до админа\n"
        "и помогу посчитать вам сумму к оплате 💸",
        reply_markup=keyboard,
    )

# ================= КНОПКА =================

async def calc_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.message.reply_text(
        "Пожалуйста, введите ваш Telegram-юзернейм\n"
        "(например: @anna)"
    )
    context.user_data["wait_username"] = True

# ================= ТЕКСТ =================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_username"):
        return

    username = update.message.text.strip().lower()
    if not username.startswith("@"):
        await update.message.reply_text("Юзернейм должен начинаться с @")
        return

    context.user_data["wait_username"] = False
    await calculate(update, context, username)

# ================= РАСЧЁТ =================

async def calculate(update, context, username):
    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1.get_all_records()

    total_kzt = 0
    total_rub = 0
    text = f"{username}\n\n"
    payment_details = None

    for box in registry:
        if str(box.get("Активна", "")).lower() != "да":
            continue

        box_name = box.get("Название коробки")
        box_url = box.get("Ссылка на таблицу")
        deadline = box.get("Дедлайн оплаты", "")
        recs = box.get("Реквизиты для оплаты", "")

        if payment_details is None and recs:
            payment_details = recs

        sheet = gc.open_by_url(box_url).sheet1
        rows = sheet.get_all_records()

        box_kzt = 0
        box_rub = 0
        lines = []

        for row in rows:
            if row.get("Ник в тг", "").lower() != username:
                continue
            if str(row.get("Статус оплаты", "")).lower() == "оплачено":
                continue

            razbor = re.sub(r"^#+", "#", str(row.get("Номер разбора", "")))
            kzt = int(row.get("Цена в тенге", 0))
            rub = int(row.get("Цена в рублях", 0))

            box_kzt += kzt
            box_rub += rub

            lines.append(
                f"{razbor} — {row.get('Название позиции')} — "
                f"{kzt} ₸ / {rub} ₽"
            )

        if not lines:
            continue

        total_kzt += box_kzt
        total_rub += box_rub

        text += f"📦 {box_name}\n"
        text += "\n".join(lines)
        text += f"\nИтого по коробке: {box_kzt} ₸ / {box_rub} ₽\n"

        if deadline:
            text += f"\n⏰ Дедлайн оплаты:\n{deadline}\n"

        text += "\n"

    if total_kzt == 0 and total_rub == 0:
        await update.message.reply_text(
            "У вас нет неоплаченных позиций в активных коробках ✅"
        )
        return

    if payment_details:
        text += f"\n💳 Реквизиты для оплаты:\n{payment_details}"

    text += (
        f"\n\n💰 *Общая сумма к оплате:*\n"
        f"*{total_kzt} ₸ / {total_rub} ₽*"
    )

    await update.message.reply_text(text, parse_mode="Markdown")

# ================= УВЕДОМЛЕНИЯ =================

async def notify_new_boxes(app):
    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
    rows = registry.get_all_records()

    for box in rows:
        if str(box.get("Активна", "")).lower() != "да":
            continue
        if str(box.get("Уведомление отправлено", "")).lower() == "yes":
            continue

        text = (
            "📦 *Вышла новая коробка!*\n"
            "Проверь себя по юзернейму или я могу посчитать за тебя ❤️\n\n"
            f"{box['Название коробки']}\n"
        )

        if box.get("Дедлайн оплаты"):
            text += f"⏰ Дедлайн оплаты:\n{box['Дедлайн оплаты']}\n\n"

        text += box["Ссылка на таблицу"]

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📦 Посчитать мою сумму к оплате доставки до админа", callback_data="calc")]]
        )

        for chat_id in SUBSCRIBERS:
            await app.bot.send_message(
                chat_id,
                text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

# ================= MAIN =================

async def post_init(app):
    asyncio.create_task(notify_new_boxes(app))

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(calc_button, pattern="^calc$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.run_polling()

if __name__ == "__main__":
    main()
