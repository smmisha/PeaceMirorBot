"""Мелкие обёртки над Telegram API, где сбой не должен ломать основную работу."""

import asyncio
import logging
from telegram.error import NetworkError, TimedOut, RetryAfter, Forbidden, BadRequest

logger = logging.getLogger("PeaceMirorBot.telegram_utils")

NETWORK_RETRIES = 3
RETRY_DELAY = 2


async def with_retry(action_factory, description: str, attempts: int = NETWORK_RETRIES):
    """
    Повторяет действие при сетевом сбое. Возвращает результат или None.

    Прокси PythonAnywhere периодически отдаёт 503, и один такой сбой терял
    действие целиком: предупреждение не отправлялось, мут не применялся.

    action_factory — ФУНКЦИЯ, создающая корутину (lambda: bot.send_message(...)),
    а не готовая корутина: её нельзя дождаться дважды.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await action_factory()
        except (Forbidden, BadRequest):
            raise
        except RetryAfter as e:
            wait = getattr(e, "retry_after", RETRY_DELAY)
            logger.warning(f"{description}: лимит Telegram, ждём {wait}s")
            await asyncio.sleep(wait)
        except (NetworkError, TimedOut) as e:
            logger.warning(f"{description}: сетевой сбой ({e}), попытка {attempt}/{attempts}")
            if attempt == attempts:
                logger.error(f"{description}: не удалось после {attempts} попыток")
                return None
            await asyncio.sleep(RETRY_DELAY * attempt)
    return None


async def send_chat_action_safe(context, chat_id: int, action: str = "typing") -> bool:
    """
    Показывает индикатор «печатает…» / «отправляет фото…», молча переживая сбой.
    """
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=action)
        return True
    except Exception as e:
        logger.warning(f"Could not send chat action '{action}' to chat {chat_id}: {e}")
        return False


async def get_message_media_bytes(context, message) -> tuple[bytes | None, str]:
    """
    Downloads media (photo, sticker, or GIF animation thumbnail) from a Telegram message.
    Returns tuple: (media_bytes, media_type).
    """
    if not message:
        return None, ""

    file_id = None
    media_type = ""

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.animation:
        if message.animation.thumbnail:
            file_id = message.animation.thumbnail.file_id
        else:
            file_id = message.animation.file_id
        media_type = "animation"
    elif message.sticker:
        if message.sticker.thumbnail:
            file_id = message.sticker.thumbnail.file_id
        else:
            file_id = message.sticker.file_id
        media_type = "sticker"

    if not file_id:
        return None, ""

    try:
        tg_file = await context.bot.get_file(file_id)
        raw_bytes = await tg_file.download_as_bytearray()
        return bytes(raw_bytes), media_type
    except Exception as e:
        logger.warning(f"Failed to download media {media_type} (file_id: {file_id}): {e}")
        return None, ""

