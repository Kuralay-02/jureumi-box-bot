import os
import json
import asyncio
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

if not BOT_TOKEN or not GOOGLE_CREDENTIALS:
    raise RuntimeError("ENV variables not found")

USERS_FILE = "users.json"
BOXES_FILE = "known_boxes.json"
ASK_USERNAME = "ask_username"

REESTR_SHEET_ID = "1OoNWbRIvj23dAwVC75RMf7SrNqzGHjFuIdB-jwTntQc"

creds_dict = json.loads(GOOGLE_CREDENTIALS)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(credentials)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != ASK_USERNAME:
        return

    username = update.message.text.strip()
    if not username.startswith("@"):
        await update.message.reply_text("Введите юзернейм в формате @username")
        return

    reestr_rows = gc.open_by_key(REESTR_SHEET_ID).sheet1.get_all_records()

    result = {}
    total_kzt = total_rub = 0

    for box in reestr_rows:
        if box.get("Активна", "").lower() != "да":
            continue

        box_name = box.get("Название коробки")
        box_url = box.get("Ссылка на таблицу")
        if not box_url:
            continue

        sheet = gc.open_by_url(box_url).sheet1
        rows = sheet.get_all_records()

        for row in rows:
            if row.get("Ник в тг") != username:
                continue
            if row.get("Статус оплаты") != "не оплачено":
                continue

            result.setdefault(box_name, []).append(row)

    if not result:
        await update.message.reply_text(
            f"У {username} нет неоплаченных позиций ✅"
        )
        context.user_data.clear()
        return

    msg = f"{username}\n\n"

    for box, items in result.items():
        box_kzt = box_rub = 0
        msg += f"📦 {box}\n"

        for i in items:
            kzt = int(i.get("Цена в тенге", 0))
            rub = int(i.get("Цена в рублях", 0))
            box_kzt += kzt
            box_rub += rub
            msg += f"{i.get('Номер разбора')} — {i.get('Название позиции')} — {kzt} ₸ / {rub} ₽\n"

        msg += f"Итого по коробке: {box_kzt} ₸ / {box_rub} ₽\n\n"
        total_kzt += box_kzt
        total_rub += box_rub

    msg += f"💰 *Общая сумма к оплате:*\n*{total_kzt} ₸ / {total_rub} ₽*"
    await update.message.reply_text(msg, parse_mode="Markdown")
    context.user_data.clear()


async def notify_loop(app):
    while True:
        try:
            known = load_json(BOXES_FILE, [])
            rows = gc.open_by_key(REESTR_SHEET_ID).sheet1.get_all_records()

            active = [
                f"{r.get('Название коробки')}|{r.get('Ссылка на таблицу')}"
                for r in rows
                if r.get("Активна", "").lower() == "да"
            ]

            new = [x for x in active if x not in known]
            if new:
                users = load_json(USERS_FILE, [])
                for box in new:
                    name = box.split("|")[0]
                    for uid in users:
                        await app.bot.send_message(
                            uid, f"📦 *Новая активная коробка!*\n\n{name}", parse_mode="Markdown"
                        )
                save_json(BOXES_FILE, active)

        except Exception as e:
            print("Notify error:", e)

        await asyncio.sleep(600)


async def post_init(app):
    asyncio.create_task(notify_loop(app))


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.Regex("^📦 Посчитать мою сумму к оплате доставки до админа$"),
            ask_username,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("Bot started safely 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()
