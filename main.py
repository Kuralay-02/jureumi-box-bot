import os
import re
from datetime import datetime, timedelta

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

# ========= НАСТРОЙКИ =========

BOT_TOKEN = os.environ["BOT_TOKEN"]
REGISTRY_SHEET_URL = os.environ["REGISTRY_SHEET_URL"]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_file(
    "credentials.json",
    scopes=SCOPES
)
gc = gspread.authorize(creds)

# ========= /start =========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
    ]
    await update.message.reply_text(
        "Здравствуйте!\n"
        "Я буду уведомлять о выходе новых доставок коробок до админа\n"
        "и помогу посчитать вам сумму к оплате 💸",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# ========= КНОПКИ =========

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "calc":
        await query.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n"
            "(например: @anna)"
        )
        context.user_data["waiting_username"] = True

# ========= ВВОД ЮЗЕРНЕЙМА =========

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_username"):
        return

    username = update.message.text.strip().lower()
    if not username.startswith("@"):
        await update.message.reply_text("Введите юзернейм с @")
        return

    context.user_data["waiting_username"] = False
    await calculate_for_user(update, context, username)

# ========= РАСЧЁТ =========

async def calculate_for_user(update, context, username):
    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
    boxes = registry.get_all_records()

    total_kzt = 0
    total_rub = 0
    messages = []

    for box in boxes:
        if str(box.get("Активна", "")).lower() != "да":
            continue

        sheet = gc.open_by_url(box["Ссылка на таблицу"]).sheet1
        rows = sheet.get_all_records()

        box_kzt = 0
        box_rub = 0
        lines = []

        for row in rows:
            if row.get("Ник в тг", "").lower() != username:
                continue
            if str(row.get("Статус оплаты", "")).lower() == "оплачено":
                continue

            kzt = int(row.get("Цена в тенге", 0))
            rub = int(row.get("Цена в рублях", 0))
            razbor = row.get("Номер разбора", "").lstrip("#")

            box_kzt += kzt
            box_rub += rub

            lines.append(
                f"#{razbor} — {row.get('Название позиции')} — "
                f"{kzt} ₸ / {rub} ₽"
            )

        if not lines:
            continue

        total_kzt += box_kzt
        total_rub += box_rub

        text = (
            f"📦 {box['Название коробки']}\n"
            + "\n".join(lines)
            + f"\nИтого по коробке: {box_kzt} ₸ / {box_rub} ₽"
        )

        deadline = box.get("Дедлайн оплаты", "").strip()
        if deadline:
            text += f"\n\n⏰ Дедлайн оплаты:\n{deadline}"

        messages.append(text)

    if not messages:
        await update.message.reply_text("У вас нет неоплаченных позиций.")
        return

    await update.message.reply_text("\n\n".join(messages))
    await update.message.reply_text(
        f"💰 *Общая сумма к оплате:*\n"
        f"{total_kzt} ₸ / {total_rub} ₽",
        parse_mode="Markdown",
    )

# ========= УВЕДОМЛЕНИЕ О НОВОЙ КОРОБКЕ =========

async def notify_new_boxes(app):
    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
    rows = registry.get_all_records()

    for i, box in enumerate(rows, start=2):
        if box.get("Активна", "").lower() != "да":
            continue
        if box.get("Уведомление отправлено", "").lower() == "yes":
            continue

        text = (
            "📦 *Вышла новая коробка!*\n"
            "Проверь себя по юзернейму или я могу посчитать за тебя ❤️\n\n"
            f"{box['Название коробки']}\n"
        )

        deadline = box.get("Дедлайн оплаты", "").strip()
        if deadline:
            text += f"⏰ Дедлайн оплаты:\n{deadline}\n\n"

        text += box["Ссылка на таблицу"]

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]]
        )

        for chat_id in app.bot_data.get("subscribers", set()):
            await app.bot.send_message(
                chat_id,
                text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )

        registry.update_cell(i, 6, "yes")  # колонка F

# ========= MAIN =========

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.bot_data["subscribers"] = set()

    async def track_users(update: Update, context):
        app.bot_data["subscribers"].add(update.effective_chat.id)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL, track_users))

    app.run_polling()

if __name__ == "__main__":
    main()
