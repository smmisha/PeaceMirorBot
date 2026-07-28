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

    Раньше этот вызов стоял внутри общего try вместе с генерацией ответа: разовый
    `httpx.ProxyError: 503` от прокси PythonAnywhere на индикаторе уводил
    выполнение в except, и пользователь не получал ответ, хотя ИИ даже не
    вызывался. Индикатор — косметика, его провал не повод терять ответ.
    """
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=action)
        return True
    except Exception as e:
        logger.warning(f"Could not send chat action '{action}' to chat {chat_id}: {e}")
        return False

