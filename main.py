import os
import json
import asyncio
from datetime import datetime, timedelta
import pytz

import gspread
from oauth2client.service_account import ServiceAccountCredentials

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

# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
REGISTRY_SHEET_URL = os.getenv("REGISTRY_SHEET_URL")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

# ================== GOOGLE ==================
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

creds_dict = json.loads(GOOGLE_CREDENTIALS)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
gc = gspread.authorize(creds)

# ================== STORAGE ==================
USERS_FILE = "users.json"
NOTIFIED_FILE = "notified_boxes.json"

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

users = load_json(USERS_FILE, [])
notified_boxes = load_json(NOTIFIED_FILE, [])

# ================== HELPERS ==================
def parse_deadline(text: str):
    try:
        dt = datetime.strptime(text.strip(), "%d.%m.%Y %H:%M")
        return pytz.timezone("Asia/Almaty").localize(dt)
    except:
        return None

def bold(text):
    return f"<b>{text}</b>"

# ================== /start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in users:
        users.append(chat_id)
        save_json(USERS_FILE, users)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
    ])

    await update.message.reply_text(
        "Здравствуйте!\n"
        "Я уведомляю о выходе новых коробок и считаю сумму к оплате 💸",
        reply_markup=keyboard
    )

# ================== CALC FLOW ==================
async def calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    context.user_data["awaiting_username"] = True
    await update.callback_query.message.reply_text(
        "Пожалуйста, введите ваш Telegram-юзернейм\n(например: @anna)"
    )

async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_username"):
        return

    username = update.message.text.strip()
    if not username.startswith("@"):
        await update.message.reply_text("Юзернейм должен начинаться с @")
        return

    context.user_data["awaiting_username"] = False
    await calculate_for_user(update, context, username)

# ================== CALCULATION ==================
async def calculate_for_user(update, context, username):
    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1.get_all_records()

    total_kzt = 0
    total_rub = 0
    output = [username]

    shown_requisites = False

    for box in registry:
        if str(box["Активна"]).lower() != "да":
            continue

        box_name = box["Название коробки"]
        box_url = box["Ссылка на таблицу"]
        deadline_text = box["Дедлайн оплаты"]
        requisites = box["Реквизиты для оплаты"]

        sheet = gc.open_by_url(box_url).sheet1.get_all_records()

        box_sum_kzt = 0
        box_sum_rub = 0
        lines = []

        for row in sheet:
            if row["Ник в тг"] != username:
                continue

            kzt = int(row["Цена в тенге"] or 0)
            rub = int(row["Цена в рублях"] or 0)

            box_sum_kzt += kzt
            box_sum_rub += rub

            lines.append(
                f'#{row["Номер разбора"]} — {row["Название позиции"]} — {kzt} ₸ / {rub} ₽'
            )

        if not lines:
            continue

        total_kzt += box_sum_kzt
        total_rub += box_sum_rub

        output.append(
            f"\n📦 <b>{box_name}</b>\n"
            + "\n".join(lines)
            + f"\nИтого по коробке: {box_sum_kzt} ₸ / {box_sum_rub} ₽"
        )

        # дедлайн показываем всегда
        output.append(f"\n⏰ <b>Дедлайн оплаты:</b>\n{deadline_text}")

        # реквизиты — ТОЛЬКО 1 раз
        if not shown_requisites:
            output.append(f"\n💳 <b>Реквизиты для оплаты:</b>\n{requisites}")
            shown_requisites = True

    if total_kzt == 0 and total_rub == 0:
        await update.message.reply_text("По этому юзернейму ничего не найдено.")
        return

    output.append(
        f"\n💰 <b>Общая сумма к оплате:</b>\n"
        f"<b>{total_kzt} ₸ / {total_rub} ₽</b>"
    )

    await update.message.reply_text(
        "\n".join(output),
        parse_mode="HTML"
    )

# ================== NOTIFICATIONS ==================
async def check_new_boxes(context: ContextTypes.DEFAULT_TYPE):
    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1.get_all_records()

    for box in registry:
        if str(box["Активна"]).lower() != "да":
            continue

        box_id = box["Ссылка на таблицу"]
        if box_id in notified_boxes:
            continue

        notified_boxes.append(box_id)
        save_json(NOTIFIED_FILE, notified_boxes)

        text = (
            "📦 <b>Вышла новая коробка!</b>\n"
            "Проверь себя по юзернейму или я могу посчитать за тебя ❤️\n\n"
            f"<b>{box['Название коробки']}</b>\n"
            f"{box['Ссылка на таблицу']}\n\n"
            f"⏰ <b>Дедлайн:</b> {box['Дедлайн оплаты']}"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
        ])

        for user_id in users:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            except:
                pass

# ================== MAIN ==================
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(calc_start, pattern="^calc$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    app.job_queue.run_repeating(check_new_boxes, interval=60, first=5)

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
