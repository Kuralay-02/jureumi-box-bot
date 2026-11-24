"""Keep blocking table operations off the Telegram event loop."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sheets")


async def run_sheets(operation, *args, **kwargs):
    return await asyncio.get_running_loop().run_in_executor(
        _executor, partial(operation, *args, **kwargs)
    )


async def telegram_call(operation, *args, **kwargs):
    for attempt in range(4):
        try:
            return await operation(*args, **kwargs)
        except Exception as error:
            if (
                type(error).__name__ not in {"RetryAfter", "TelegramRetryAfter"}
                or attempt == 3
            ):
                raise
            delay = getattr(error, "retry_after", getattr(error, "timeout", 1))
            if hasattr(delay, "total_seconds"):
                delay = delay.total_seconds()
            await asyncio.sleep(float(delay) + 1)


def operation_key(prefix, data):
    import hashlib
    import json

    payload = json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        default=lambda value: (
            sorted(value) if isinstance(value, (set, frozenset)) else str(value)
        ),
    )
    return prefix + "-" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def append_once(worksheet, rows, operation_id):
    """Resume a batch append after an uncertain response using stored row keys."""
    values = worksheet.get_all_values()
    headers = values[0] if values else []
    if not headers:
        raise RuntimeError("В листе отсутствуют заголовки")
    if "ID операции" in headers:
        column = headers.index("ID операции")
    else:
        column = len(headers)
        if any(len(row) > column for row in rows):
            raise RuntimeError("Заголовки листа короче записываемых строк")
        worksheet.update_cell(1, column + 1, "ID операции")
    saved = {row[column] for row in values[1:] if len(row) > column and row[column]}
    missing = []
    for index, row in enumerate(rows):
        key = f"{operation_id}/{index}"
        if key in saved:
            continue
        expanded = list(row) + [""] * max(0, column + 1 - len(row))
        expanded[column] = key
        missing.append(expanded)
    if missing:
        worksheet.append_rows(missing, value_input_option="RAW")
    return len(missing)


def configure_client(client):
    client.set_timeout((5, 20))
    return client


from telegram.ext import BaseUpdateProcessor
from weakref import WeakValueDictionary


class ChatUpdateProcessor(BaseUpdateProcessor):
    """Process independent chats concurrently while preserving each form's order."""

    def __init__(self, max_concurrent_updates=16):
        super().__init__(max_concurrent_updates)
        self._chat_locks = WeakValueDictionary()

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_process_update(self, update, coroutine):
        chat = getattr(update, "effective_chat", None)
        key = chat.id if chat else None
        lock = self._chat_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[key] = lock
        async with lock:
            await coroutine
