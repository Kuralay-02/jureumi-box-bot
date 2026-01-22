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

# 👉 ID РЕЕСТРА КОРОБОК
REESTR_SHEET_ID = "1OoNWbRIvj23dAwVC75RMf7SrNqzGHjFuIdB-jwTntQc"

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text(
            "Введите юзернейм в формате @username"
        )
        return

    reestr_rows = gc.open_by_key(REESTR_SHEET_ID).sheet1.get_all_records()

    result = {}
    total_kzt = 0
    total_rub = 0

    # ===== проходим по активным коробкам =====
    for box in reestr_rows:
        if box.get("Активна", "").lower() != "да":
            continue

        box_name = box.get("Название коробки")
        box_url = box.get("Ссылка на таблицу")

        if not box_url:
            continue

        sheet = gc.open_by_url(box_url).sheet1
        rows = sheet.get_all_records()

        # ===== ищем позиции пользователя =====
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

    # ===== формируем ответ =====
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

            # номер разбора выводим КАК ЕСТЬ (без добавления #)
            razbor = str(item.get("Номер разбора", "")).strip()

            message += (
                f"{razbor} — {item.get('Название позиции')} — "
                f"{kzt} ₸ / {rub} ₽\n"
            )

        message += (
            f"Итого по коробке: {box_sum_kzt} ₸ / {box_sum_rub} ₽\n\n"
        )

        total_kzt += box_sum_kzt
        total_rub += box_sum_rub

    message += (
        f"💰 Общая сумма к оплате:\n"
        f"{total_kzt} ₸ / {total_rub} ₽"
    )

    await update.message.reply_text(message)
    context.user_data.clear()


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

    print("Bot is fully ready 🚀")
    app.run_polling()


if __name__ == "__main__":
    main()
