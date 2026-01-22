import os
import asyncio
from datetime import datetime
from telegram import (
    Update,
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
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========= НАСТРОЙКИ =========
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")
REGISTRY_SHEET_URL = os.getenv("REGISTRY_SHEET_URL")

# ========= GOOGLE =========
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
creds = ServiceAccountCredentials.from_json_keyfile_dict(
    eval(GOOGLE_CREDENTIALS), scope
)
gc = gspread.authorize(creds)

# ========= ХРАНИЛИЩА =========
SUBSCRIBERS = set()
KNOWN_BOXES = set()

# ========= /start =========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    SUBSCRIBERS.add(chat_id)

    keyboard = [
        [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
    ]
    await update.message.reply_text(
        "Здравствуйте!\n"
        "Я буду уведомлять о выходе новых доставок коробок до админа\n"
        "и помогу посчитать вам сумму к оплате 💸",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

# ========= КНОПКА =========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "calc":
        await query.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n"
            "(например: @anna)"
        )

# ========= ПОДСЧЁТ =========
async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if not username.startswith("@"):
        await update.message.reply_text("Юзернейм должен начинаться с @")
        return

    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1.get_all_records()

    total_kzt = 0
    total_rub = 0
    lines = []
    shown_deadline = False
    shown_requisites = False

    for box in registry:
        if str(box["Активна"]).lower() != "да":
            continue

        sheet = gc.open_by_url(box["Ссылка на таблицу"]).sheet1
        rows = sheet.get_all_records(expected_headers=[
            "Номер разбора",
            "Название позиции",
            "Ник в тг",
            "Цена в тенге",
            "Цена в рублях",
            "Статус оплаты",
        ])

        box_sum_kzt = 0
        box_sum_rub = 0
        box_lines = []

        for row in rows:
            if row["Ник в тг"].strip() != username:
                continue
            if str(row["Статус оплаты"]).lower() == "оплачено":
                continue

            razbor = str(row["Номер разбора"]).replace("##", "#")
            kzt = int(row["Цена в тенге"] or 0)
            rub = int(row["Цена в рублях"] or 0)

            box_sum_kzt += kzt
            box_sum_rub += rub

            box_lines.append(
                f"{razbor} — {row['Название позиции']} — {kzt} ₸ / {rub} ₽"
            )

        if not box_lines:
            continue

        lines.append(f"\n📦 *{box['Название коробки']}*\n" + "\n".join(box_lines))
        lines.append(
            f"_Итого по коробке:_ {box_sum_kzt} ₸ / {box_sum_rub} ₽"
        )

        total_kzt += box_sum_kzt
        total_rub += box_sum_rub

        if box.get("Дедлайн оплаты") and not shown_deadline:
            lines.append(
                f"\n⏰ *Дедлайн оплаты:*\n{box['Дедлайн оплаты']}"
            )
            shown_deadline = True

        if box.get("Реквизиты для оплаты") and not shown_requisites:
            lines.append(
                f"\n💳 *Реквизиты для оплаты:*\n{box['Реквизиты для оплаты']}"
            )
            shown_requisites = True

    if not lines:
        await update.message.reply_text("По этому юзернейму ничего не найдено.")
        return

    lines.append(
        f"\n💰 *Общая сумма к оплате:*\n*{total_kzt} ₸ / {total_rub} ₽*"
    )

    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown"
    )

# ========= УВЕДОМЛЕНИЯ =========
async def check_new_boxes(context: ContextTypes.DEFAULT_TYPE):
    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1.get_all_records()

    for box in registry:
        if str(box["Активна"]).lower() != "да":
            continue

        name = box["Название коробки"]
        if name in KNOWN_BOXES:
            continue

        KNOWN_BOXES.add(name)

        text = (
            "📦 *Вышла новая коробка!*\n"
            "Проверь себя по юзернейму или я могу посчитать за тебя ❤️\n\n"
            f"{name}\n"
        )

        if box.get("Дедлайн оплаты"):
            text += f"⏰ Дедлайн оплаты:\n{box['Дедлайн оплаты']}\n\n"

        text += box["Ссылка на таблицу"]

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
        ])

        for chat_id in SUBSCRIBERS:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
            )

# ========= MAIN =========
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    app.job_queue.run_repeating(check_new_boxes, interval=60, first=1)

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
