import os
import json
import asyncio

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
        reply_markup=keyboard,
    )

# ================== BUTTON ==================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "calc":
        context.user_data["waiting_username"] = True
        await query.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n(например: @anna)"
        )

# ================== USERNAME ==================
async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_username"):
        return

    username = update.message.text.strip().lower()
    if not username.startswith("@"):
        username = "@" + username

    context.user_data["waiting_username"] = False

    try:
        registry_sheet = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
        registry_rows = registry_sheet.get_all_records()
    except Exception:
        await update.message.reply_text("Ошибка доступа к реестру 😢")
        return

    found_rows = []

    # === ГЛАВНОЕ: идём по РЕЕСТРУ → КОРОБКАМ ===
    for reg in registry_rows:
        active = str(reg.get("Активна", "")).strip().lower()
        if active != "да":
            continue

        box_url = reg.get("Ссылка на таблицу")
        if not box_url:
            continue

        try:
            box_sheet = gc.open_by_url(box_url).sheet1
            box_rows = box_sheet.get_all_values()[1:]  # без заголовков
        except Exception:
            continue

        for r in box_rows:
            if len(r) < 5:
                continue

            tg_nick = str(r[2]).strip().lower()  # колонка C
            if tg_nick == username:
                found_rows.append(r)

    if not found_rows:
        await update.message.reply_text(
            f"По юзернейму {username} я ничего не нашла 🤍"
        )
        return

    total_kzt = 0
    total_rub = 0
    lines = []

    for r in found_rows:
        box_num = r[0]
        name = r[1]

        try:
            kzt = int(str(r[3]).replace(" ", "") or 0)
        except:
            kzt = 0

        try:
            rub = int(str(r[4]).replace(" ", "") or 0)
        except:
            rub = 0

        total_kzt += kzt
        total_rub += rub

        lines.append(
            f"• {box_num} — {name}\n  {kzt} ₸ / {rub} ₽"
        )

    text = (
        f"📦 Найдено позиций: {len(found_rows)}\n\n"
        + "\n\n".join(lines)
        + f"\n\n💰 Итого:\n{total_kzt} ₸ / {total_rub} ₽"
    )

    await update.message.reply_text(text)

# ================== MAIN ==================
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
