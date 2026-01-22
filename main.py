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

BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not found")

if not GOOGLE_CREDENTIALS:
    raise RuntimeError("GOOGLE_CREDENTIALS not found")

ASK_USERNAME = "ask_username"

# --- Google setup ---
creds_dict = json.loads(GOOGLE_CREDENTIALS)
scopes = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(credentials)

REESTR_SHEET_ID = "1OoNWbRIvj23dAwVC75RMf7SrNqzGHjFuIdB-jwTntQc"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["📦 Посчитать мою сумму к оплате доставки до админа"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

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
    state = context.user_data.get("state")

    if state != ASK_USERNAME:
        await update.message.reply_text("Пожалуйста, нажмите кнопку ниже 👇")
        return

    username = update.message.text.strip()

    if not username.startswith("@") or len(username) < 3:
        await update.message.reply_text(
            "Пожалуйста, введите юзернейм в формате @username"
        )
        return

    # --- Читаем реестр коробок ---
    sh = gc.open_by_key(REESTR_SHEET_ID)
    ws = sh.sheet1
    rows = ws.get_all_records()

    active_boxes = [
        row["Название коробки"]
        for row in rows
        if row.get("Активна", "").lower() == "да"
    ]

    if not active_boxes:
        await update.message.reply_text("Сейчас нет активных коробок.")
    else:
        boxes_text = "\n".join(f"• {box}" for box in active_boxes)
        await update.message.reply_text(
            f"Юзернейм {username} принят ✅\n\n"
            f"Активные коробки:\n{boxes_text}"
        )

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

    print("Bot started with Google access...")
    app.run_polling()


if __name__ == "__main__":
    main()
