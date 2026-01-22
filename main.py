import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN not found")

ASK_USERNAME = "ask_username"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        ["📦 Посчитать мою сумму к оплате доставки до админа"]
    ]
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await update.message.reply_text(
        "Здравствуйте!\n"
        "Я буду уведомлять о выходе новых доставок коробок до админа\n"
        "и помогу посчитать вам сумму к оплате 💸",
        reply_markup=reply_markup
    )

async def ask_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = ASK_USERNAME
    await update.message.reply_text(
        "Пожалуйста, введите ваш Telegram-юзернейм\n"
        "(например: @anna)"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state == ASK_USERNAME:
        username = update.message.text.strip()

        if not username.startswith("@") or len(username) < 3:
            await update.message.reply_text(
                "Пожалуйста, введите юзернейм в формате @username"
            )
            return

        await update.message.reply_text(
            f"Юзернейм {username} принят ✅\n"
            "Скоро я посчитаю сумму к оплате."
        )

        context.user_data.clear()
    else:
        await update.message.reply_text(
            "Пожалуйста, нажмите кнопку ниже 👇"
        )

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(
            filters.Regex("^📦 Посчитать мою сумму к оплате доставки до админа$"),
            ask_username
        )
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    print("Bot started with button...")
    app.run_polling()

if __name__ == "__main__":
    main()
