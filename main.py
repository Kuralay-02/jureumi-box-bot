import os
import json

import gspread
from google.oauth2.service_account import Credentials

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove,
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
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# ================== GOOGLE ==================
creds_dict = json.loads(GOOGLE_CREDS_JSON)
scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]]
    )

    await update.message.reply_text(
        "Здравствуйте!\n\nЯ помогу посчитать сумму к оплате.\nНажмите кнопку ниже 👇",
        reply_markup=keyboard
    )

# ================== BUTTON ==================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "calc":
        context.user_data["waiting_username"] = True
        await query.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n(например: @anna)",
            reply_markup=ReplyKeyboardRemove()
        )

# ================== USERNAME ==================
async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_username"):
        return

    username = update.message.text.strip().lower()
    if not username.startswith("@"):
        username = "@" + username

    context.user_data["waiting_username"] = False

    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
    registry_rows = registry.get_all_records()

    total_kzt = 0
    total_rub = 0
    found_any = False
    box_blocks = []

    requisites_text = None

    for reg in registry_rows:
        if str(reg.get("Активна", "")).lower() != "да":
            continue

        box_name = reg.get("Название коробки")
        sheet_url = reg.get("Ссылка на таблицу")
        deadline = reg.get("Дедлайн оплаты")
        requisites = reg.get("Реквизиты для оплаты")

        if requisites and not requisites_text:
            requisites_text = requisites

        try:
            sheet = gc.open_by_url(sheet_url).sheet1
            rows = sheet.get_all_values()[1:]
        except Exception:
            continue

        box_lines = []
        box_kzt = 0
        box_rub = 0

        for r in rows:
            if len(r) < 5:
                continue

            tg_nick = str(r[2]).strip().lower()
            if tg_nick != username:
                continue

            found_any = True

            num = r[0]
            name = r[1]
            kzt = int(r[3]) if r[3] else 0
            rub = int(r[4]) if r[4] else 0

            box_kzt += kzt
            box_rub += rub

            box_lines.append(
                f"• {num} — {name}\n  {kzt} ₸ / {rub} ₽"
            )

        if box_lines:
            total_kzt += box_kzt
            total_rub += box_rub

            block = (
                f"📦 **{box_name}**\n"
                + "\n".join(box_lines)
                + f"\n⏰ Дедлайн: {deadline}"
                + "\n🧾 Чек отправить в @jureumireceiptsbot"
            )

            box_blocks.append(block)

    if not found_any:
        await update.message.reply_text(
            f"По юзернейму {username} я ничего не нашла 🤍"
        )
        return

    final_text = f"👤 **{username}**\n\n"
    final_text += "\n\n".join(box_blocks)

    if requisites_text:
        final_text += f"\n\n💳 **Реквизиты для оплаты:**\n{requisites_text}"

    final_text += f"\n\n💰 **Итого к оплате:**\n**{total_kzt} ₸ / {total_rub} ₽**"

    await update.message.reply_text(final_text, parse_mode="Markdown")


# ================== MAIN ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    app.run_polling()

if __name__ == "__main__":
    main()

