import os
import json
import re
import unicodedata

import gspread
from google.oauth2.service_account import Credentials

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# ================== SUBSCRIBERS ==================

SUBSCRIBERS_FILE = "subscribers.json"
SUBSCRIBERS = set()


def load_subscribers():
    global SUBSCRIBERS

    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            SUBSCRIBERS = {
                int(value)
                for value in data
                if str(value).strip().isdigit()
            }
        else:
            SUBSCRIBERS = set()

    except FileNotFoundError:
        SUBSCRIBERS = set()

    except Exception as error:
        print(f"Не удалось загрузить subscribers.json: {error}")
        SUBSCRIBERS = set()


def save_subscribers():
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as file:
            json.dump(
                sorted(SUBSCRIBERS),
                file,
                ensure_ascii=False,
                indent=2,
            )

    except Exception as error:
        print(f"Не удалось сохранить subscribers.json: {error}")


def ensure_subscriber(chat_id: int):
    if not chat_id:
        return

    if chat_id not in SUBSCRIBERS:
        SUBSCRIBERS.add(chat_id)
        save_subscribers()


# ================== HELPERS ==================

USERNAME_RE = re.compile(
    r"(?i)(?:^|[^a-z0-9_@])(@?[a-z0-9_]{5,32})(?:$|[^a-z0-9_])"
)


def normalize_username(value):
    """
    Все варианты:

    @tepl0o0
    tepl0o0
    Tepl0o0
    @Tepl0o0

    преобразуются в:

    @tepl0o0
    """

    if not value:
        return ""

    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u00A0", " ")
    text = text.replace("\u200b", "")
    text = text.replace("\ufeff", "")
    text = text.strip()

    text = (
        text
        .replace("https://t.me/", "")
        .replace("http://t.me/", "")
        .replace("t.me/", "")
    )

    match = USERNAME_RE.search(text)

    if not match:
        return ""

    username = match.group(1).lower()

    if not username.startswith("@"):
        username = "@" + username

    return username


def escape_md(text: str) -> str:
    if text is None:
        return ""

    value = str(text)
    value = value.replace("\\", "\\\\")
    value = value.replace("*", "\\*")
    value = value.replace("_", "\\_")
    value = value.replace("[", "\\[")
    value = value.replace("`", "\\`")

    return value


def split_message(text: str, limit: int = 4000) -> list[str]:
    parts = []
    remaining = text or ""

    while len(remaining) > limit:
        chunk = remaining[:limit]
        cut_position = chunk.rfind("\n")

        if cut_position <= 0:
            cut_position = limit

        parts.append(remaining[:cut_position])
        remaining = remaining[cut_position:].lstrip("\n")

    if remaining:
        parts.append(remaining)

    return parts


def parse_int(value) -> int:
    text = str(value).strip().replace(" ", "")

    if not text:
        return 0

    try:
        return int(float(text))

    except Exception:
        return 0


def parse_chat_id(value):
    text = str(value).strip().replace(" ", "")

    if not text:
        return None

    try:
        return int(float(text))

    except Exception:
        return None


# ================== PERMANENT MENU ==================

CALC_BUTTON = "📦 Посчитать мою сумму"
NOTIFY_BUTTON = "📣 Разослать уведомление"
PAYMENT_REMINDER_BUTTON = "🔔 Напомнить об оплате"


def build_main_menu(chat_id: int):
    """
    Постоянная нижняя клавиатура.

    Обычным пользователям доступен только подсчёт суммы.
    Админские кнопки видны только ADMIN_CHAT_ID.
    """

    buttons = [[CALC_BUTTON]]

    if chat_id == ADMIN_CHAT_ID:
        buttons.append([NOTIFY_BUTTON])
        buttons.append([PAYMENT_REMINDER_BUTTON])

    return ReplyKeyboardMarkup(
        buttons,
        resize_keyboard=True,
        is_persistent=True,
    )


# ================== ENV ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
REGISTRY_SHEET_URL = os.getenv("REGISTRY_SHEET_URL")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

ADMIN_ID = int(os.getenv("ADMIN_ID"))
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID"))


# ================== GOOGLE ==================

creds_dict = json.loads(GOOGLE_CREDS_JSON)

scopes = [
    "https://www.googleapis.com/auth/spreadsheets"
]

creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=scopes,
)

gc = gspread.authorize(creds)


def get_registry_spreadsheet():
    return gc.open_by_url(REGISTRY_SHEET_URL)


def get_registry_sheet():
    return get_registry_spreadsheet().sheet1


def get_users_sheet():
    return get_registry_spreadsheet().worksheet("Пользователи")


def get_users_map():
    users_sheet = get_users_sheet()
    rows = users_sheet.get_all_records()

    users_map = {}

    for row in rows:
        username = normalize_username(row.get("Username", ""))
        chat_id = parse_chat_id(row.get("Chat ID", ""))

        if not username or not chat_id:
            continue

        users_map[username] = chat_id

    return users_map


def save_user_to_sheet(username: str, chat_id: int):
    """
    Сохраняет пользователя в лист «Пользователи».

    Не создаёт дубли:
    - если найден тот же Chat ID — обновляет username;
    - если найден тот же username — обновляет Chat ID;
    - если найдено несколько совпадений — оставляет одну строку;
    - если пользователя нет — добавляет новую строку.
    """

    username = normalize_username(username)
    chat_id = parse_chat_id(chat_id)

    if not username or not chat_id:
        return

    users_sheet = get_users_sheet()
    rows = users_sheet.get_all_values()

    matched_rows = []

    for row_number, row in enumerate(rows[1:], start=2):
        existing_username = normalize_username(
            row[0] if len(row) > 0 else ""
        )
        existing_chat_id = parse_chat_id(
            row[1] if len(row) > 1 else ""
        )

        if (
            existing_username == username
            or existing_chat_id == chat_id
        ):
            matched_rows.append(row_number)

    if not matched_rows:
        users_sheet.append_row(
            [username, chat_id],
            value_input_option="RAW",
        )
        return

    main_row = matched_rows[0]

    users_sheet.update(
        range_name=f"A{main_row}:B{main_row}",
        values=[[username, chat_id]],
        value_input_option="RAW",
    )

    for duplicate_row in reversed(matched_rows[1:]):
        users_sheet.delete_rows(duplicate_row)


# ================== NEW BOXES ==================

def get_new_boxes_from_registry():
    registry_sheet = get_registry_sheet()
    rows = registry_sheet.get_all_records()

    new_boxes = []

    for row in rows:
        is_active = str(
            row.get("Активна", "")
        ).strip().lower()

        notification_sent = str(
            row.get("Уведомление отправлено", "")
        ).strip().lower()

        if is_active != "да":
            continue

        if notification_sent == "да":
            continue

        new_boxes.append(
            {
                "name": row.get("Название коробки"),
                "link": row.get("Ссылка на таблицу"),
                "deadline": row.get("Дедлайн оплаты"),
            }
        )

    return new_boxes


def build_box_notification_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📦 Посчитать сумму",
                    callback_data="calc",
                )
            ]
        ]
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


# ================== UNPAID POSITIONS ==================

def collect_unpaid_positions():
    registry_sheet = get_registry_sheet()
    registry_rows = registry_sheet.get_all_records()

    unpaid_users = {}

    for registry_row in registry_rows:
        is_active = str(
            registry_row.get("Активна", "")
        ).strip().lower()

        if is_active != "да":
            continue

        box_name = registry_row.get("Название коробки", "")
        sheet_url = registry_row.get("Ссылка на таблицу", "")
        deadline = registry_row.get("Дедлайн оплаты", "")

        if not sheet_url:
            continue

        try:
            box_sheet = gc.open_by_url(sheet_url).sheet1
            rows = box_sheet.get_all_values()[1:]

        except Exception as error:
            print(f"Не удалось открыть коробку {box_name}: {error}")
            continue

        box_users = {}

        for row in rows:
            if len(row) < 3:
                continue

            username = normalize_username(row[2])

            if not username:
                continue

            payment_status = ""

            if len(row) > 5:
                payment_status = str(row[5]).strip().lower()

            if payment_status == "оплачено":
                continue

            number = row[0] if len(row) > 0 else ""
            position_name = row[1] if len(row) > 1 else ""
            kzt = parse_int(row[3]) if len(row) > 3 else 0
            rub = parse_int(row[4]) if len(row) > 4 else 0

            if username not in box_users:
                box_users[username] = {
                    "positions": [],
                    "total_kzt": 0,
                    "total_rub": 0,
                }

            box_users[username]["positions"].append(
                {
                    "number": number,
                    "name": position_name,
                    "kzt": kzt,
                    "rub": rub,
                }
            )

            box_users[username]["total_kzt"] += kzt
            box_users[username]["total_rub"] += rub

        for username, box_data in box_users.items():
            if username not in unpaid_users:
                unpaid_users[username] = {
                    "boxes": [],
                    "total_kzt": 0,
                    "total_rub": 0,
                    "positions_count": 0,
                }

            unpaid_users[username]["boxes"].append(
                {
                    "name": box_name,
                    "deadline": deadline,
                    "positions": box_data["positions"],
                    "total_kzt": box_data["total_kzt"],
                    "total_rub": box_data["total_rub"],
                }
            )

            unpaid_users[username]["total_kzt"] += box_data["total_kzt"]
            unpaid_users[username]["total_rub"] += box_data["total_rub"]
            unpaid_users[username]["positions_count"] += len(
                box_data["positions"]
            )

    return unpaid_users


def build_payment_reminder_text(username, user_data):
    safe_username = escape_md(username)

    text = (
        "🔔 **Напоминание об оплате**\n\n"
        f"По юзернейму **{safe_username}** "
        "остались неоплаченные позиции:\n\n"
    )

    box_blocks = []

    for box in user_data["boxes"]:
        safe_box_name = escape_md(box["name"])
        safe_deadline = escape_md(box["deadline"])

        block = f"📦 **{safe_box_name}**\n"

        position_lines = []

        for position in box["positions"]:
            safe_number = escape_md(position["number"])
            safe_name = escape_md(position["name"])

            position_lines.append(
                f"• {safe_number} — {safe_name}\n"
                f"  {position['kzt']} ₸ / {position['rub']} ₽"
            )

        block += "\n".join(position_lines)

        if safe_deadline:
            block += f"\n⏰ Дедлайн: {safe_deadline}"

        box_blocks.append(block)

    text += "\n\n".join(box_blocks)

    text += (
        "\n\n━━━━━━━━━━━━\n"
        "💰 **Итого к оплате:**\n"
        f"**{user_data['total_kzt']} ₸ / "
        f"{user_data['total_rub']} ₽**\n\n"
        "Пожалуйста, оплатите позиции и отправьте чек "
        "по правилам шопа."
    )

    return text


def build_payment_preview(unpaid_users, users_map):
    with_chat_id = []
    without_chat_id = []

    for username, user_data in sorted(unpaid_users.items()):
        preview_line = (
            f"• {escape_md(username)} — "
            f"{user_data['positions_count']} поз. — "
            f"{user_data['total_kzt']} ₸ / "
            f"{user_data['total_rub']} ₽"
        )

        if username in users_map:
            with_chat_id.append(preview_line)
        else:
            without_chat_id.append(preview_line)

    text = (
        "🔔 **Предпросмотр напоминания об оплате**\n\n"
        f"👥 Всего пользователей с неоплаченными позициями: "
        f"**{len(unpaid_users)}**\n"
        f"✅ Найдены в листе «Пользователи»: "
        f"**{len(with_chat_id)}**\n"
        f"❌ Нет Chat ID: "
        f"**{len(without_chat_id)}**\n\n"
    )

    if with_chat_id:
        text += (
            "✅ **Получатели:**\n"
            + "\n".join(with_chat_id)
            + "\n\n"
        )

    if without_chat_id:
        text += (
            "❌ **Нет в базе пользователей:**\n"
            + "\n".join(without_chat_id)
        )

    return text


# ================== START ==================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id
    ensure_subscriber(chat_id)

    await update.message.reply_text(
        "Здравствуйте!\n\n"
        "Я помогу посчитать сумму к оплате.\n"
        "Выберите нужное действие в меню 👇",
        reply_markup=build_main_menu(chat_id),
    )


# ================== BUTTONS ==================

async def on_button(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    ensure_subscriber(chat_id)

    if query.data == "calc":
        context.user_data["waiting_username"] = True

        await query.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n"
            "(например: @anna)",
            reply_markup=build_main_menu(chat_id),
        )

    elif query.data == "notify":
        if chat_id != ADMIN_CHAT_ID:
            await query.message.reply_text(
                "❌ Недостаточно прав"
            )
            return

        try:
            boxes = get_new_boxes_from_registry()

        except Exception as error:
            await query.message.reply_text(
                "❌ Не удалось прочитать реестр коробок.\n\n"
                f"Ошибка: {error}"
            )
            return

        if not boxes:
            await query.message.reply_text(
                "❌ Нет новых коробок для уведомления"
            )
            return

        context.user_data["notify_boxes"] = boxes

        keyboard = []

        for index, box in enumerate(boxes):
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"📦 {box['name']}",
                        callback_data=f"notify_box_{index}",
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="cancel",
                )
            ]
        )

        await query.message.reply_text(
            "Выберите коробку для отправки уведомления:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif query.data.startswith("notify_box_"):
        if chat_id != ADMIN_CHAT_ID:
            await query.message.reply_text(
                "❌ Недостаточно прав"
            )
            return

        boxes = context.user_data.get(
            "notify_boxes",
            [],
        )

        try:
            index = int(query.data.split("_")[-1])

        except Exception:
            await query.message.reply_text(
                "❌ Ошибка выбора коробки"
            )
            return

        if index < 0 or index >= len(boxes):
            await query.message.reply_text(
                "❌ Ошибка выбора коробки"
            )
            return

        box = boxes[index]
        text = build_box_notification_text(box)
        keyboard = build_box_notification_keyboard()

        sent = 0
        failed = 0

        await query.message.reply_text(
            "⏳ Начинаю рассылку уведомления..."
        )

        for subscriber_id in list(SUBSCRIBERS):
            try:
                await context.bot.send_message(
                    chat_id=subscriber_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )

                sent += 1

            except Exception as error:
                failed += 1
                print(
                    f"Не удалось отправить "
                    f"{subscriber_id}: {error}"
                )

        await query.message.reply_text(
            "✅ Уведомление отправлено\n\n"
            f"📦 {box['name']}\n"
            f"✅ Доставлено: {sent}\n"
            f"❌ Не доставлено: {failed}"
        )

    elif query.data == "payment_reminder_preview":
        if chat_id != ADMIN_CHAT_ID:
            await query.message.reply_text(
                "❌ Недостаточно прав"
            )
            return

        await query.message.reply_text(
            "⏳ Проверяю неоплаченные позиции "
            "во всех активных коробках..."
        )

        try:
            unpaid_users = collect_unpaid_positions()
            users_map = get_users_map()

        except gspread.WorksheetNotFound:
            await query.message.reply_text(
                "❌ Не найден лист «Пользователи».\n\n"
                "Проверь, что второй лист называется точно:\n"
                "Пользователи"
            )
            return

        except Exception as error:
            await query.message.reply_text(
                "❌ Не удалось выполнить проверку.\n\n"
                f"Ошибка: {error}"
            )
            return

        if not unpaid_users:
            await query.message.reply_text(
                "✅ Во всех активных коробках "
                "стоит отметка «ОПЛАЧЕНО».\n"
                "Напоминать некому."
            )
            return

        preview_text = build_payment_preview(
            unpaid_users,
            users_map,
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Отправить напоминания",
                        callback_data="payment_reminder_send",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Отмена",
                        callback_data="cancel",
                    )
                ],
            ]
        )

        preview_parts = split_message(preview_text)

        for index, part in enumerate(preview_parts):
            is_last_part = index == len(preview_parts) - 1

            await query.message.reply_text(
                part,
                parse_mode="Markdown",
                reply_markup=(
                    keyboard
                    if is_last_part
                    else None
                ),
            )

    elif query.data == "payment_reminder_send":
        if chat_id != ADMIN_CHAT_ID:
            await query.message.reply_text(
                "❌ Недостаточно прав"
            )
            return

        await query.message.reply_text(
            "⏳ Повторно проверяю актуальные оплаты "
            "и начинаю рассылку..."
        )

        try:
            unpaid_users = collect_unpaid_positions()
            users_map = get_users_map()

        except Exception as error:
            await query.message.reply_text(
                "❌ Не удалось выполнить рассылку.\n\n"
                f"Ошибка: {error}"
            )
            return

        if not unpaid_users:
            await query.message.reply_text(
                "✅ Неоплаченных позиций больше нет."
            )
            return

        sent_users = []
        missing_users = []
        failed_users = []

        keyboard = build_box_notification_keyboard()

        for username, user_data in sorted(
            unpaid_users.items()
        ):
            target_chat_id = users_map.get(username)

            if not target_chat_id:
                missing_users.append(username)
                continue

            reminder_text = build_payment_reminder_text(
                username,
                user_data,
            )

            reminder_parts = split_message(reminder_text)

            try:
                for index, part in enumerate(reminder_parts):
                    is_last_part = (
                        index == len(reminder_parts) - 1
                    )

                    await context.bot.send_message(
                        chat_id=target_chat_id,
                        text=part,
                        parse_mode="Markdown",
                        reply_markup=(
                            keyboard
                            if is_last_part
                            else None
                        ),
                        disable_web_page_preview=True,
                    )

                sent_users.append(username)

            except Exception as error:
                error_text = str(error).strip()

                if not error_text:
                    error_text = error.__class__.__name__

                failed_users.append(
                    {
                        "username": username,
                        "reason": error_text,
                    }
                )

                print(
                    f"Не удалось отправить напоминание "
                    f"{username} ({target_chat_id}): {error}"
                )

        report_text = (
            "📋 **Отчёт о рассылке напоминаний**\n\n"
            f"✅ Доставлено: **{len(sent_users)}**\n"
            f"❌ Нет Chat ID: **{len(missing_users)}**\n"
            f"⚠️ Ошибка отправки: "
            f"**{len(failed_users)}**"
        )

        if sent_users:
            report_text += (
                "\n\n✅ **Доставлено:**\n"
                + "\n".join(
                    f"• {escape_md(username)}"
                    for username in sent_users
                )
            )

        if missing_users:
            report_text += (
                "\n\n❌ **Нет в листе «Пользователи»:**\n"
                + "\n".join(
                    f"• {escape_md(username)}"
                    for username in missing_users
                )
            )

        if failed_users:
            report_text += (
                "\n\n⚠️ **Не удалось отправить:**\n"
            )

            for failed_user in failed_users:
                safe_username = escape_md(
                    failed_user["username"]
                )

                safe_reason = escape_md(
                    failed_user["reason"]
                )

                report_text += (
                    f"• {safe_username} — "
                    f"{safe_reason}\n"
                )

        for part in split_message(report_text):
            await query.message.reply_text(
                part,
                parse_mode="Markdown",
            )

    elif query.data == "cancel":
        await query.message.reply_text(
            "❌ Действие отменено"
        )


# ================== USERNAME SEARCH ==================

async def handle_username(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id
    ensure_subscriber(chat_id)

    message_text = (update.message.text or "").strip()

    # Постоянная кнопка подсчёта суммы
    if message_text == CALC_BUTTON:
        context.user_data["waiting_username"] = True

        await update.message.reply_text(
            "Пожалуйста, введите ваш Telegram-юзернейм\n"
            "(например: @anna)",
            reply_markup=build_main_menu(chat_id),
        )
        return

    # Постоянная админская кнопка рассылки новой коробки
    if message_text == NOTIFY_BUTTON:
        if chat_id != ADMIN_CHAT_ID:
            return

        try:
            boxes = get_new_boxes_from_registry()

        except Exception as error:
            await update.message.reply_text(
                "❌ Не удалось прочитать реестр коробок.\n\n"
                f"Ошибка: {error}",
                reply_markup=build_main_menu(chat_id),
            )
            return

        if not boxes:
            await update.message.reply_text(
                "❌ Нет новых коробок для уведомления",
                reply_markup=build_main_menu(chat_id),
            )
            return

        context.user_data["notify_boxes"] = boxes

        keyboard = []

        for index, box in enumerate(boxes):
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f"📦 {box['name']}",
                        callback_data=f"notify_box_{index}",
                    )
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton(
                    "❌ Отмена",
                    callback_data="cancel",
                )
            ]
        )

        await update.message.reply_text(
            "Выберите коробку для отправки уведомления:",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # Постоянная админская кнопка ручного напоминания об оплате
    if message_text == PAYMENT_REMINDER_BUTTON:
        if chat_id != ADMIN_CHAT_ID:
            return

        await update.message.reply_text(
            "⏳ Проверяю неоплаченные позиции "
            "во всех активных коробках...",
            reply_markup=build_main_menu(chat_id),
        )

        try:
            unpaid_users = collect_unpaid_positions()
            users_map = get_users_map()

        except gspread.WorksheetNotFound:
            await update.message.reply_text(
                "❌ Не найден лист «Пользователи».\n\n"
                "Проверь, что второй лист называется точно:\n"
                "Пользователи",
                reply_markup=build_main_menu(chat_id),
            )
            return

        except Exception as error:
            await update.message.reply_text(
                "❌ Не удалось выполнить проверку.\n\n"
                f"Ошибка: {error}",
                reply_markup=build_main_menu(chat_id),
            )
            return

        if not unpaid_users:
            await update.message.reply_text(
                "✅ Во всех активных коробках "
                "стоит отметка «ОПЛАЧЕНО».\n"
                "Напоминать некому.",
                reply_markup=build_main_menu(chat_id),
            )
            return

        preview_text = build_payment_preview(
            unpaid_users,
            users_map,
        )

        confirm_keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Отправить напоминания",
                        callback_data="payment_reminder_send",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Отмена",
                        callback_data="cancel",
                    )
                ],
            ]
        )

        preview_parts = split_message(preview_text)

        for index, part in enumerate(preview_parts):
            is_last_part = index == len(preview_parts) - 1

            await update.message.reply_text(
                part,
                parse_mode="Markdown",
                reply_markup=(
                    confirm_keyboard
                    if is_last_part
                    else build_main_menu(chat_id)
                ),
            )
        return

    if not context.user_data.get("waiting_username"):
        return

    username = normalize_username(update.message.text)
    context.user_data["waiting_username"] = False

    if not username:
        await update.message.reply_text(
            "Не смогла распознать юзернейм.\n"
            "Пришли, пожалуйста, в формате @username"
        )
        return

    try:
        save_user_to_sheet(username, chat_id)

    except Exception as error:
        print(
            f"Не удалось сохранить пользователя "
            f"{username} ({chat_id}): {error}"
        )

    await update.message.reply_text(
        "⏳ Проверяю позиции по указанному юзернейму..."
    )

    try:
        registry = get_registry_sheet()
        registry_rows = registry.get_all_records()

    except Exception as error:
        await update.message.reply_text(
            "❌ Не удалось открыть реестр коробок.\n\n"
            f"Ошибка: {error}"
        )
        return

    total_kzt = 0
    total_rub = 0

    found_unpaid = False
    found_paid = False

    unpaid_box_blocks = []
    paid_box_blocks = []

    requisites_text = None
    safe_username = escape_md(username)

    for registry_row in registry_rows:
        is_active = str(
            registry_row.get("Активна", "")
        ).strip().lower()

        if is_active != "да":
            continue

        box_name = registry_row.get(
            "Название коробки",
            "",
        )

        sheet_url = registry_row.get(
            "Ссылка на таблицу",
            "",
        )

        deadline = registry_row.get(
            "Дедлайн оплаты",
            "",
        )

        requisites = registry_row.get(
            "Реквизиты для оплаты",
            "",
        )

        if requisites and not requisites_text:
            requisites_text = requisites

        if not sheet_url:
            continue

        try:
            sheet = gc.open_by_url(sheet_url).sheet1
            rows = sheet.get_all_values()[1:]

        except Exception as error:
            print(
                f"Не удалось открыть коробку "
                f"{box_name}: {error}"
            )
            continue

        unpaid_lines = []
        paid_lines = []

        box_kzt = 0
        box_rub = 0

        for row in rows:
            if len(row) < 3:
                continue

            table_username = normalize_username(row[2])

            if table_username != username:
                continue

            number = escape_md(
                row[0] if len(row) > 0 else ""
            )

            position_name = escape_md(
                row[1] if len(row) > 1 else ""
            )

            kzt = parse_int(
                row[3] if len(row) > 3 else 0
            )

            rub = parse_int(
                row[4] if len(row) > 4 else 0
            )

            payment_status = str(
                row[5] if len(row) > 5 else ""
            ).strip().lower()

            if payment_status == "оплачено":
                found_paid = True

                paid_lines.append(
                    f"• {number} — {position_name}\n"
                    f"  ✅ ОПЛАЧЕНО"
                )

            else:
                found_unpaid = True

                box_kzt += kzt
                box_rub += rub

                unpaid_lines.append(
                    f"• {number} — {position_name}\n"
                    f"  {kzt} ₸ / {rub} ₽"
                )

        safe_box_name = escape_md(box_name)
        safe_deadline = escape_md(deadline)

        if unpaid_lines:
            total_kzt += box_kzt
            total_rub += box_rub

            block = (
                f"📦 **{safe_box_name}**\n"
                + "\n".join(unpaid_lines)
            )

            if safe_deadline:
                block += (
                    f"\n⏰ Дедлайн: {safe_deadline}"
                )

            unpaid_box_blocks.append(block)

        if paid_lines:
            paid_block = (
                f"📦 **{safe_box_name}**\n"
                + "\n".join(paid_lines)
            )

            paid_box_blocks.append(paid_block)

    if not found_unpaid and not found_paid:
        await update.message.reply_text(
            f"По юзернейму {safe_username} "
            "я ничего не нашла в активных коробках 🤍",
            parse_mode="Markdown",
        )
        return

    if found_unpaid:
        unpaid_text = (
            f"👤 **{safe_username}**\n\n"
            "💳 **Неоплаченные позиции:**\n\n"
            + "\n\n".join(unpaid_box_blocks)
        )

        for part in split_message(unpaid_text):
            await update.message.reply_text(
                part,
                parse_mode="Markdown",
            )

    else:
        await update.message.reply_text(
            f"👤 **{safe_username}**\n\n"
            "✅ В активных коробках у вас нет "
            "неоплаченных позиций.",
            parse_mode="Markdown",
        )

    if found_paid:
        paid_text = (
            "✅ **Оплаченные разборы "
            "в активных коробках:**\n\n"
            + "\n\n".join(paid_box_blocks)
        )

        for part in split_message(paid_text):
            await update.message.reply_text(
                part,
                parse_mode="Markdown",
            )

    if found_unpaid:
        summary_text = ""

        if requisites_text:
            summary_text += (
                "💳 **Реквизиты для оплаты:**\n"
                f"{escape_md(requisites_text)}\n\n"
            )

        summary_text += (
            "━━━━━━━━━━━━\n"
            "💰 **Итого к оплате:**\n"
            f"**{total_kzt} ₸ / {total_rub} ₽**"
        )

        await update.message.reply_text(
            summary_text,
            parse_mode="Markdown",
        )


# ================== MAIN ==================

def main():
    load_subscribers()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(on_button)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_username,
        )
    )

    app.run_polling()


if __name__ == "__main__":
    main()
