import os
import json
import asyncio
import re
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from telegram import (
    Update,
    ReplyKeyboardMarkup,
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

USERS_FILE = "users.json"
BOXES_FILE = "known_boxes.json"

ASK_USERNAME = "ask_username"

REESTR_SHEET_ID = "1OoNWbRIvj23dAwVC75RMf7SrNqzGHjFuIdB-jwTntQc"

credentials = Credentials.from_service_account_info(
    json.loads(GOOGLE_CREDENTIALS),
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ],
)
gc = gspread.authorize(credentials)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def deadline_active(text: str) -> bool:
    if not text:
        return False
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", text)
    if not m:
        return True
    try:
        return datetime.now() <= datetime.strptime(m.group(1), "%d.%m.%Y")
    except Exception:
        return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    users = load_json(USERS_FILE, [])
    if chat_id not in users:
        users.append(chat_id)
        save_json(USERS_FILE, users)

    keyboard = [["📦 Посчитать мою сумму к оплате доставки до админа"]]
    await update.message.reply_text(
        "Здравствуйте!\n"
        "Я буду уведомлять о выходе новых доставок коробок до админа\n"
        "и помогу посчитать вам сумму к оплате 💸",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
    )


async def ask_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = ASK_USERNAME
    await update.message.reply_text(
        "Пожалуйста, введите ваш Telegram-юзернейм\n(например: @anna)"
    )


async def calc_from_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["state"] = ASK_USERNAME
    await query.message.reply_text(
        "Пожалуйста, введите ваш Telegram-юзернейм\n(например: @anna)"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != ASK_USERNAME:
        return

    username = update.message.text.strip()
    if not username.startswith("@"):
        await update.message.reply_text("Введите юзернейм в формате @username")
        return

    reestr = gc.open_by_key(REESTR_SHEET_ID).sheet1.get_all_records()

    result = {}
    meta = {}
    total_kzt = total_rub = 0

    for box in reestr:
        if box.get("Активна", "").lower() != "да":
            continue

        name = box.get("Название коробки")
        url = box.get("Ссылка на таблицу")
        if not name or not url:
            continue

        meta[name] = {
            "deadline": box.get("Дедлайн оплаты", ""),
            "payment": box.get("Реквизиты для оплаты", ""),
        }

        rows = gc.open_by_url(url).sheet1.get_all_records()

        for r in rows:
            if r.get("Ник в тг") == username and r.get("Статус оплаты") == "не оплачено":
                result.setdefault(name, []).append(r)

    if not result:
        await update.message.reply_text(
            f"У {username} нет неоплаченных позиций в активных коробках ✅"
        )
        context.user_data.clear()
        return

    text = f"{username}\n\n"

    for box_name, items in result.items():
        box_kzt = box_rub = 0
        text += f"📦 {box_name}\n"

        for i in items:
            kzt = int(i.get("Цена в тенге", 0))
            rub = int(i.get("Цена в рублях", 0))
            box_kzt += kzt
            box_rub += rub

            text += (
                f"{i.get('Номер разбора')} — {i.get('Название позиции')} — "
                f"{kzt} ₸ / {rub} ₽\n"
            )

        text += f"Итого по коробке: {box_kzt} ₸ / {box_rub} ₽\n"

        info = meta.get(box_name, {})
        if deadline_active(info.get("deadline", "")):
            if info.get("deadline"):
                text += f"\n⏰ Дедлайн оплаты:\n{info['deadline']}\n"
            if info.get("payment"):
                text += f"\n💳 Реквизиты для оплаты:\n{info['payment']}\n"

        text += "\n"
        total_kzt += box_kzt
        total_rub += box_rub

    text += f"💰 *Общая сумма к оплате:*\n*{total_kzt} ₸ / {total_rub} ₽*"

    await update.message.reply_text(text, parse_mode="Markdown")
    context.user_data.clear()


async def notify_loop(app):
    while True:
        try:
            known = load_json(BOXES_FILE, [])
            rows = gc.open_by_key(REESTR_SHEET_ID).sheet1.get_all_records()

            active = [
                f"{r['Название коробки']}|{r['Ссылка на таблицу']}"
                for r in rows
                if r.get("Активна", "").lower() == "да"
            ]

            new = [b for b in active if b not in known]

            if new:
                users = load_json(USERS_FILE, [])
                for b in new:
                    name, link = b.split("|", 1)
                    msg = (
                        "📦 Вышла новая коробка! Проверь себя по юзернейму "
                        "или я могу посчитать за тебя ❤️\n\n"
                        f"{name}\n{link}"
                    )
                    keyboard = InlineKeyboardMarkup(
                        [[InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]]
                    )
                    for u in users:
                        await app.bot.send_message(
                            u,
                            msg,
                            reply_markup=keyboard,
                            disable_web_page_preview=True,
                        )

                save_json(BOXES_FILE, active)

        except Exception as e:
            print("Notify error:", e)

        await asyncio.sleep(600)


async def post_init(app):
    asyncio.create_task(notify_loop(app))


def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.Regex("^📦 Посчитать мою сумму к оплате доставки до админа$"),
            ask_username,
        )
    )
    app.add_handler(CallbackQueryHandler(calc_from_notification, pattern="^calc$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot started safely 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()
