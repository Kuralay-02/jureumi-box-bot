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

ADMIN_CHAT_ID = 635801439
SUBSCRIBERS = set()

def normalize_username(u):
    if not u:
        return ""
    u = str(u).strip().lower()
    if not u.startswith("@"):
        u = "@" + u
    return u
    
# ================== ENV ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
REGISTRY_SHEET_URL = os.getenv("REGISTRY_SHEET_URL")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))

# ================== GOOGLE ==================
creds_dict = json.loads(GOOGLE_CREDS_JSON)
scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
gc = gspread.authorize(creds)

def get_new_boxes_from_registry():
    sheet = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
    rows = sheet.get_all_records()

    new_boxes = []

    for row in rows:
        if str(row.get("Активна", "")).strip().lower() != "да":
            continue

        if str(row.get("Уведомление отправлено", "")).strip().lower() == "да":
            continue

        new_boxes.append({
            "name": row.get("Название коробки"),
            "link": row.get("Ссылка на таблицу"),
            "deadline": row.get("Дедлайн оплаты"),
        })

    return new_boxes
    
import re
from datetime import datetime, timedelta
import pytz

MSK_TZ = pytz.timezone("Europe/Moscow")


def parse_deadline_msk(deadline_text: str) -> datetime | None:
    """
    Из строки вида:
    '23:00 по ACT / 21:00 по МСК 01.02.2026'
    извлекает дедлайн как datetime в МСК
    """

    if not deadline_text:
        return None

    # ищем время после "по МСК"
    time_match = re.search(r"(\d{1,2}:\d{2})\s*по\s*МСК", deadline_text)
    date_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", deadline_text)

    if not time_match or not date_match:
        return None

    time_part = time_match.group(1)   # 21:00
    date_part = date_match.group(1)   # 01.02.2026

    dt_str = f"{date_part} {time_part}"

    naive_dt = datetime.strptime(dt_str, "%d.%m.%Y %H:%M")
    return MSK_TZ.localize(naive_dt)

def should_send_24h_reminder(deadline_dt: datetime) -> bool:
    now_msk = datetime.now(MSK_TZ)
    return timedelta(hours=0) <= (deadline_dt - now_msk) <= timedelta(hours=24)

def get_boxes_for_24h_reminder():
    sheet = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
    rows = sheet.get_all_records()

    boxes = []

    for row in rows:
        if str(row.get("Активна", "")).strip().lower() != "да":
            continue

        if str(row.get("Напоминание за 24ч отправлено", "")).strip().lower() == "да":
            continue

        deadline_text = row.get("Дедлайн оплаты", "")
        deadline_dt = parse_deadline_msk(deadline_text)

        if not deadline_dt:
            continue

        if should_send_24h_reminder(deadline_dt):
            boxes.append({
                "name": row.get("Название коробки"),
                "link": row.get("Ссылка на таблицу"),
                "deadline": deadline_text,
            })

    return boxes

def build_24h_reminder_text(box):
    name = box["name"]
    link = box["link"]
    deadline = box["deadline"]

    return (
        "⏰ **Напоминание! До дедлайна осталось 24 часа**\n\n"
        "Проверь себя по юзернейму ❤️\n\n"
        f"📦 **[{name}]({link})**\n\n"
        f"⏳ Дедлайн оплаты: {deadline}\n\n"
        "👉 Нажми кнопку ниже, чтобы посчитать сумму к оплате"
    )

def build_box_notification_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📦 Посчитать сумму", callback_data="calc")]]
    )

def build_box_notification_text(box):
    name = box["name"]
    link = box["link"]
    deadline = box["deadline"]

    return (
        "📦 **Вышла новая коробка!**\n"
        "Проверь себя по юзернейму ❤️\n\n"
        f"📦 **[{name}]({link})**\n"
        f"⏰ Дедлайн: {deadline}\n\n"
        "👉 Нажми кнопку ниже, чтобы посчитать сумму"
    )
    
def build_box_notification_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📦 Посчитать сумму", callback_data="calc")]]
    )




# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    SUBSCRIBERS.add(chat_id)

    buttons = [
        [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
    ]

    if chat_id == ADMIN_CHAT_ID:
        buttons.append(
            [InlineKeyboardButton("📣 Разослать уведомление", callback_data="notify")]
        )
        buttons.append(
            [InlineKeyboardButton("🔔 Напомнить за 24 часа", callback_data="remind_24h_preview")]
        )

    keyboard = InlineKeyboardMarkup(buttons)

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

    elif query.data == "notify":
        if update.effective_chat.id != ADMIN_CHAT_ID:
            await query.answer("Недостаточно прав", show_alert=True)
            return

        boxes = get_new_boxes_from_registry()

        if not boxes:
            await query.message.reply_text(
                "❌ Нет новых коробок для уведомления"
            )
            return

        text = "📦 Коробки, готовые к уведомлению:\n\n"

        for box in boxes:
            text += (
                f"• **[{box['name']}]({box['link']})**\n"
                f"  ⏰ Дедлайн: {box['deadline']}\n\n"
            )

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
       
        for box in boxes:
            text = build_box_notification_text(box)
            keyboard = build_box_notification_keyboard()

            for chat_id in SUBSCRIBERS:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    print(f"Не удалось отправить {chat_id}: {e}")

        await query.message.reply_text("✅ Уведомления отправлены подписчикам")

    elif query.data == "remind_24h_preview":
        if update.effective_chat.id != ADMIN_CHAT_ID:
            await query.answer("Недостаточно прав", show_alert=True)
            return

        boxes = get_boxes_for_24h_reminder()

        if not boxes:
            await query.message.reply_text("⏰ Нет коробок для напоминания за 24 часа")
            return

        text = "🔔 **Напоминание за 24 часа — предпросмотр**\n\n"

        for box in boxes:
            text += (
                f"• **[{box['name']}]({box['link']})**\n"
                f"⏰ Дедлайн: {box['deadline']}\n\n"
            )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Отправить напоминание", callback_data="remind_24h_send")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ])

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=keyboard
        )

    elif query.data == "cancel":
        await query.message.reply_text("❌ Действие отменено")

    elif query.data == "remind_24h_send":
        if update.effective_chat.id != ADMIN_CHAT_ID:
            await query.answer("Недостаточно прав", show_alert=True)
            return

        boxes = get_boxes_for_24h_reminder()

        if not boxes:
            await query.message.reply_text("⏰ Нет коробок для напоминания за 24 часа")
            return

        sent = 0

        for box in boxes:
            text = (
                "⏰ **Напоминание! Осталось 24 часа до дедлайна**\n\n"
                f"📦 **[{box['name']}]({box['link']})**\n"
                f"🕒 Дедлайн: {box['deadline']}\n\n"
                "👉 Проверь себя по юзернейму и не забудь оплатить"
            )

            keyboard = build_box_notification_keyboard()

            for chat_id in SUBSCRIBERS:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    print(f"Не удалось отправить {chat_id}: {e}")

            sent += 1

        await query.message.reply_text(
            f"✅ Напоминания отправлены\n📦 Коробок: {sent}"
        )



# ================== USERNAME ==================
async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_username"):
        return
        
    username = normalize_username(update.message.text)

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

            tg_nick = normalize_username(r[2])
            user_nick = normalize_username(username)
            
            if tg_nick != user_nick:
                continue

            payment_status = str(r[5]).strip().lower() if len(r) > 5 else ""
            if payment_status == "оплачено":
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

    final_text += (
        f"\n\n💰 **Итого к оплате:**\n"
        f"**{total_kzt} ₸ / {total_rub} ₽**"
    )

    await update.message.reply_text(
        final_text,
        parse_mode="Markdown"
    )


# ================== MAIN ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    app.run_polling()

if __name__ == "__main__":
    main()

