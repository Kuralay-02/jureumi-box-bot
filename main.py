import os
import json
import re
import unicodedata
from datetime import datetime, timedelta

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

import pytz

# ================== SUBSCRIBERS (PERSIST) ==================

SUBSCRIBERS_FILE = "subscribers.json"
SUBSCRIBERS = set()

def load_subscribers():
    global SUBSCRIBERS
    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            SUBSCRIBERS = set(int(x) for x in data if str(x).strip().isdigit())
        else:
            SUBSCRIBERS = set()
    except FileNotFoundError:
        SUBSCRIBERS = set()
    except Exception:
        # если файл битый/пустой — просто стартуем с пустого списка
        SUBSCRIBERS = set()

def save_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(SUBSCRIBERS)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Не удалось сохранить subscribers: {e}")

def ensure_subscriber(chat_id: int):
    """Добавляет chat_id в подписчики и сохраняет на диск."""
    if not chat_id:
        return
    if chat_id not in SUBSCRIBERS:
        SUBSCRIBERS.add(chat_id)
        save_subscribers()

# ================== HELPERS ==================

# Более “строгий” поиск username внутри любой строки (t.me/..., "@user_name оплата", скобки и т.п.)
USERNAME_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9_@])(@?[a-z0-9_]{5,32})(?:$|[^a-z0-9_])"
)

def normalize_username(u):
    """
    Нормализует username:
    - убирает невидимые символы/нестандартные пробелы
    - понимает t.me/username, https://t.me/username
    - вытаскивает username из строк типа '@user__name оплата'
    - возвращает '@username' в lower
    """
    if not u:
        return ""

    s = unicodedata.normalize("NFKC", str(u))
    s = s.replace("\u00A0", " ")   # NBSP
    s = s.replace("\u200b", "")    # zero-width space
    s = s.replace("\ufeff", "")    # BOM
    s = s.strip()

    s = s.replace("https://t.me/", "").replace("http://t.me/", "").replace("t.me/", "")

    m = USERNAME_RE.search(s)
    if not m:
        return ""

    s = m.group(1).lower()
    if not s.startswith("@"):
        s = "@" + s
    return s

def escape_md(text: str) -> str:
    """Экранирует спецсимволы Telegram Markdown (legacy), чтобы не ломался парсинг."""
    if text is None:
        return ""
    s = str(text)
    s = s.replace("\\", "\\\\")
    s = s.replace("*", "\\*")
    s = s.replace("_", "\\_")
    s = s.replace("[", "\\[")
    s = s.replace("`", "\\`")
    return s

def split_message(text: str, limit: int = 4000) -> list[str]:
    """Режет длинный текст на части для Telegram (лимит ~4096), стараясь резать по переносам."""
    parts = []
    text = text or ""
    while len(text) > limit:
        chunk = text[:limit]
        cut = chunk.rfind("\n")
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        parts.append(text)
    return parts

def parse_int(x) -> int:
    """Мягко парсим суммы (на случай '1 000', '1000.0', пусто)."""
    s = str(x).strip().replace(" ", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except Exception:
        return 0

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

# ================== DEADLINES / REMINDERS ==================
MSK_TZ = pytz.timezone("Europe/Moscow")

def parse_deadline_msk(deadline_text: str) -> datetime | None:
    """
    Из строки вида:
    '23:00 по ACT / 21:00 по МСК 01.02.2026'
    извлекает дедлайн как datetime в МСК
    """
    if not deadline_text:
        return None

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

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_subscriber(chat_id)

    buttons = [
        [InlineKeyboardButton("📦 Посчитать мою сумму", callback_data="calc")]
    ]

    if chat_id == ADMIN_CHAT_ID:
        buttons.append([InlineKeyboardButton("📣 Разослать уведомление", callback_data="notify")])
        buttons.append([InlineKeyboardButton("🔔 Напомнить за 24 часа", callback_data="remind_24h_preview")])

    keyboard = InlineKeyboardMarkup(buttons)

    await update.message.reply_text(
        "Здравствуйте!\n\nЯ помогу посчитать сумму к оплате.\nНажмите кнопку ниже 👇",
        reply_markup=keyboard
    )

# ================== BUTTON ==================
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    ensure_subscriber(chat_id)  # ✅ подписываем даже если человек пришёл по кнопке/пересланному сообщению

    if query.data == "calc":
        context.user_data["waiting_username"] = True
        await query.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n(например: @anna)",
            reply_markup=ReplyKeyboardRemove()
        )

    # ====== ADMIN: choose which box to notify ======
    elif query.data == "notify":
        if chat_id != ADMIN_CHAT_ID:
            await query.answer("Недостаточно прав", show_alert=True)
            return

        boxes = get_new_boxes_from_registry()

        if not boxes:
            await query.message.reply_text("❌ Нет новых коробок для уведомления")
            return

        context.user_data["notify_boxes"] = boxes

        keyboard = []
        for i, box in enumerate(boxes):
            keyboard.append([InlineKeyboardButton(f"📦 {box['name']}", callback_data=f"notify_box_{i}")])
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel")])

        await query.message.reply_text(
            "Выберите коробку для отправки уведомления:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data.startswith("notify_box_"):
        if chat_id != ADMIN_CHAT_ID:
            await query.answer("Недостаточно прав", show_alert=True)
            return

        boxes = context.user_data.get("notify_boxes", [])
        try:
            index = int(query.data.split("_")[-1])
        except Exception:
            await query.message.reply_text("Ошибка выбора коробки")
            return

        if index < 0 or index >= len(boxes):
            await query.message.reply_text("Ошибка выбора коробки")
            return

        box = boxes[index]
        text = build_box_notification_text(box)
        keyboard = build_box_notification_keyboard()

        sent = 0
        for sub_id in list(SUBSCRIBERS):
            try:
                await context.bot.send_message(
                    chat_id=sub_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                sent += 1
            except Exception as e:
                print(f"Не удалось отправить {sub_id}: {e}")

        await query.message.reply_text(
            f"✅ Уведомление отправлено\n📦 {box['name']}\n👥 Получателей: {sent}"
        )

    # ====== ADMIN: 24h reminder preview/send (как у тебя было) ======
    elif query.data == "remind_24h_preview":
        if chat_id != ADMIN_CHAT_ID:
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
        if chat_id != ADMIN_CHAT_ID:
            await query.answer("Недостаточно прав", show_alert=True)
            return

        boxes = get_boxes_for_24h_reminder()

        if not boxes:
            await query.message.reply_text("⏰ Нет коробок для напоминания за 24 часа")
            return

        sent_boxes = 0

        for box in boxes:
            text = (
                "⏰ **Напоминание! Осталось 24 часа до дедлайна**\n\n"
                f"📦 **[{box['name']}]({box['link']})**\n"
                f"🕒 Дедлайн: {box['deadline']}\n\n"
                "👉 Проверь себя по юзернейму и не забудь оплатить"
            )

            keyboard = build_box_notification_keyboard()

            for sub_id in list(SUBSCRIBERS):
                try:
                    await context.bot.send_message(
                        chat_id=sub_id,
                        text=text,
                        reply_markup=keyboard,
                        parse_mode="Markdown",
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    print(f"Не удалось отправить {sub_id}: {e}")

            sent_boxes += 1

        await query.message.reply_text(f"✅ Напоминания отправлены\n📦 Коробок: {sent_boxes}")

# ================== USERNAME ==================
async def handle_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ✅ даже если человек не нажимал /start, но пишет юзер — мы сохраняем его chat_id
    ensure_subscriber(update.effective_chat.id)

    if not context.user_data.get("waiting_username"):
        return

    username = normalize_username(update.message.text)
    context.user_data["waiting_username"] = False

    if not username:
        await update.message.reply_text("Не смогла распознать юзернейм. Пришли, пожалуйста, в формате @username")
        return

    registry = gc.open_by_url(REGISTRY_SHEET_URL).sheet1
    registry_rows = registry.get_all_records()

    total_kzt = 0
    total_rub = 0
    found_any = False
    box_blocks = []

    requisites_text = None

    safe_username = escape_md(username)

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

        user_nick = username  # уже нормализован

        for r in rows:
            if len(r) < 5:
                continue

            tg_nick = normalize_username(r[2])
            if tg_nick != user_nick:
                continue

            payment_status = str(r[5]).strip().lower() if len(r) > 5 else ""
            if payment_status == "оплачено":
                continue

            found_any = True

            num = escape_md(r[0])
            name = escape_md(r[1])
            kzt = parse_int(r[3])
            rub = parse_int(r[4])

            box_kzt += kzt
            box_rub += rub

            box_lines.append(f"• {num} — {name}\n  {kzt} ₸ / {rub} ₽")

        if box_lines:
            total_kzt += box_kzt
            total_rub += box_rub

            safe_box_name = escape_md(box_name)
            safe_deadline = escape_md(deadline)

            block = (
                f"📦 **{safe_box_name}**\n"
                + "\n".join(box_lines)
                + f"\n⏰ Дедлайн: {safe_deadline}"
            )
            box_blocks.append(block)

    if not found_any:
        await update.message.reply_text(
            f"По юзернейму {safe_username} я ничего не нашла 🤍",
            parse_mode="Markdown"
        )
        return

    positions_text = f"👤 **{safe_username}**\n\n"
    positions_text += "\n\n".join(box_blocks)

# сначала отправляем позиции (разбивая на части)
    for part in split_message(positions_text):
        await update.message.reply_text(part, parse_mode="Markdown")

# потом отдельным последним сообщением итог
    summary_text = ""

    if requisites_text:
        summary_text += f"💳 **Реквизиты для оплаты:**\n{escape_md(requisites_text)}\n\n"

    summary_text += (
        "━━━━━━━━━━━━\n"
        f"💰 **Итого к оплате:**\n"
        f"**{total_kzt} ₸ / {total_rub} ₽**"
    )

    await update.message.reply_text(summary_text, parse_mode="Markdown")

# ================== MAIN ==================
def main():
    # ✅ поднимаем подписчиков из файла при старте
    load_subscribers()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_username))

    app.run_polling()

if __name__ == "__main__":
    main()
