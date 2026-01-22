import os
import json
import asyncio
from datetime import datetime, timedelta, timezone

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

BOT_TOKEN = os.environ["BOT_TOKEN"]
REGISTRY_SHEET_URL = os.environ["REGISTRY_SHEET_URL"]

# ================== GOOGLE SHEETS ==================

def get_gspread_client():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS"])

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_info(
        creds_dict,
        scopes=scopes,
    )

    return gspread.authorize(credentials)

gc = get_gspread_client()

# ================== ВСПОМОГАТЕЛЬНОЕ ==================

def now_utc():
    return datetime.now(timezone.utc)

def parse_deadline(text: str):
    try:
        # ожидаемый формат: 01.02.2026 23:00
        return datetime.strptime(text.strip(), "%d.%m.%Y %H:%M").replace(tzinfo=timezone.utc)
    except Exception:
        return None

# ================== /start ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
    ]

    await update.message.reply_text(
        "Здравствуйте!\n"
        "Я уведомляю о новых коробках и считаю сумму к оплате 💸",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    context.bot_data.setdefault("users", set()).add(update.effective_chat.id)

# ================== КНОПКА ==================

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "calc":
        await query.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n(например: @anna)"
        )

# ================== РАСЧЁТ СУММЫ ==================

async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip().lower()

    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
    boxes = registry.get_all_records()

    total_kzt = 0
    total_rub = 0
    output = []

    shown_requisites = False

    for box in boxes:
        if box.get("Активна", "").lower() != "да":
            continue

        sheet_url = box.get("Ссылка на таблицу")
        box_name = box.get("Название коробки")
        deadline_text = box.get("Дедлайн оплаты", "")
        requisites = box.get("Реквизиты для оплаты", "")

        try:
            sheet = gc.open_by_url(sheet_url).sheet1
            rows = sheet.get_all_records()
        except Exception:
            continue

        box_sum_kzt = 0
        box_sum_rub = 0
        lines = []

        for row in rows:
            row_user = str(row.get("Юзер", "")).lower()
            if row_user != username:
                continue

            price_kzt = int(row.get("Цена тг", 0) or 0)
            price_rub = int(row.get("Цена руб", 0) or 0)

            box_sum_kzt += price_kzt
            box_sum_rub += price_rub

            lines.append(
                f"• {row.get('Позиция','')} — {price_kzt} ₸ / {price_rub} ₽"
            )

        if not lines:
            continue

        total_kzt += box_sum_kzt
        total_rub += box_sum_rub

        block = [
            f"📦 {box_name}",
            *lines,
            f"Итого по коробке: {box_sum_kzt} ₸ / {box_sum_rub} ₽",
        ]

        if deadline_text:
            deadline = parse_deadline(deadline_text)
            if deadline and deadline > now_utc():
                block.append(f"⏰ Дедлайн оплаты: {deadline_text}")

        if requisites and not shown_requisites:
            block.append(f"\n💳 Реквизиты для оплаты:\n{requisites}")
            shown_requisites = True

        output.append("\n".join(block))

    if not output:
        await update.message.reply_text("По вашему юзернейму ничего не найдено.")
        return

    message = "\n\n".join(output)
    message += f"\n\n💰 **Общая сумма к оплате:**\n{total_kzt} ₸ / {total_rub} ₽"

    await update.message.reply_text(message, parse_mode="Markdown")

# ================== УВЕДОМЛЕНИЕ О КОРОБКАХ ==================

async def notify_new_boxes(context: ContextTypes.DEFAULT_TYPE):
    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
    boxes = registry.get_all_records()

    sent = context.bot_data.setdefault("sent_boxes", set())
    users = context.bot_data.get("users", set())

    for box in boxes:
        if box.get("Активна", "").lower() != "да":
            continue

        name = box.get("Название коробки")
        link = box.get("Ссылка на таблицу")
        key = f"{name}|{link}"

        if key in sent:
            continue

        sent.add(key)

        text = (
            "📦 **Вышла новая коробка!**\n"
            "Проверь себя по юзернейму или я могу посчитать за тебя ❤️\n\n"
            f"{name}\n{link}"
        )

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]]
        )

        for chat_id in users:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                )
            except Exception:
                pass

# ================== ЗАПУСК ==================

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    app.job_queue.run_repeating(notify_new_boxes, interval=60, first=5)

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
