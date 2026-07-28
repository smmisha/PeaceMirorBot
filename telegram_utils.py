"""Мелкие обёртки над Telegram API, где сбой не должен ломать основную работу."""

import logging

logger = logging.getLogger("PeaceMirorBot.telegram_utils")


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
