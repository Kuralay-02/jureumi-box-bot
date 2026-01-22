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

# ================= ENV =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

if not BOT_TOKEN or not GOOGLE_CREDENTIALS:
    raise RuntimeError("ENV variables not found")

# ================= FILES =================
USERS_FILE = "users.json"
BOXES_FILE = "known_boxes.json"

# ================= STATE =================
ASK_USERNAME = "ask_username"

# ================= GOOGLE =================
creds_dict = json.loads(GOOGLE_CREDENTIALS)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(credentials)

# 👉 ID реестра коробок
REESTR_SHEET_ID = "1OoNWbRIvj23dAwVC75RMf7SrNqzGHjFuIdB-jwTntQc"

# ================= HELPERS =================
def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    users = load_json(USERS_FILE, [])
    if chat_id not in users:
        users.append(chat_id)
        save_json(USERS_FILE, users)

    keyboard = [["📦 Посчитать мою сумму к оплате доставки до админа"]]
    reply_markup = ReplyKeyboardMarkup(
        keyboard, resize_keyboard=True, one_time_keyboard=True
    )

    await update.message.reply_text(
        "Здравствуйте!\n"
        "Я буду уведомлять о выходе новых доставок коробок до админа\n"
        "и помогу посчитать вам сумму к оплате 💸",
        reply_markup=reply_markup,
    )

async def ask_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = ASK_USERNAME
    await update.message.reply_text(
        "Пожалуйста, введите ваш Telegram-юзернейм\n"
        "(например: @anna)"
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
    box_meta = {}
    total_kzt = 0
    total_rub = 0

    for box in reestr_rows:
        if box.get("Активна", "").lower() != "да":
            continue

        box_name = box.get("Название коробки")
        box_url = box.get("Ссылка на таблицу")
        if not box_url:
            continue

        box_meta[box_name] = {
            "deadline": box.get("Дедлайн оплаты", ""),
            "payment": box.get("Реквизиты для оплаты", ""),
        }

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
            f"У {username} нет неоплаченных позиций в активных коробках ✅"
        )
        context.user_data.clear()
        return

    message = f"{username}\n\n"

    for box_name, items in result.items():
        box_sum_kzt = 0
        box_sum_rub = 0

        message += f"📦 {box_name}\n"

        for item in items:
            kzt = int(item.get("Цена в тенге", 0))
            rub = int(item.get("Цена в рублях", 0))
            box_sum_kzt += kzt
            box_sum_rub += rub

            razbor = str(item.get("Номер разбора", "")).strip()
            message += (
                f"{razbor} — {item.get('Название позиции')} — "
                f"{kzt} ₸ / {rub} ₽\n"
            )

        message += f"Итого по коробке: {box_sum_kzt} ₸ / {box_sum_rub} ₽\n"

        meta = box_meta.get(box_name, {})
        if meta.get("deadline"):
            message += f"\n⏰ Дедлайн оплаты:\n{meta['deadline']}\n"
        if meta.get("payment"):
            message += f"\n💳 Реквизиты для оплаты:\n{meta['payment']}\n"

        message += "\n"
        total_kzt += box_sum_kzt
        total_rub += box_sum_rub

    message += (
        f"💰 *Общая сумма к оплате:*\n"
        f"*{total_kzt} ₸ / {total_rub} ₽*"
    )

    await update.message.reply_text(message, parse_mode="Markdown")
    context.user_data.clear()

# ================= NOTIFICATIONS =================
async def check_new_boxes(context: ContextTypes.DEFAULT_TYPE):
    known = load_json(BOXES_FILE, [])
    rows = gc.open_by_key(REESTR_SHEET_ID).sheet1.get_all_records()

    current_active = [
        f"{r.get('Название коробки')}|{r.get('Ссылка на таблицу')}"
        for r in rows
        if r.get("Активна", "").lower() == "да"
    ]

    new_boxes = [b for b in current_active if b not in known]

    if new_boxes:
        users = load_json(USERS_FILE, [])
        for box in new_boxes:
            name = box.split("|")[0]
            text = f"📦 *Новая активная коробка!*\n\n{name}"

            for user_id in users:
                try:
                    await context.bot.send_message(
                        chat_id=int(user_id),
                        text=text,
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

        save_json(BOXES_FILE, current_active)

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.Regex("^📦 Посчитать мою сумму к оплате доставки до админа$"),
            ask_username,
        )
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    # 🔔 проверка новых коробок каждые 10 минут
    app.job_queue.run_repeating(check_new_boxes, interval=600, first=30)

    print("Bot is running correctly 🚀")
    app.run_polling()

if __name__ == "__main__":
    main()
