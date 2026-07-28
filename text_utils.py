"""Хелперы для безопасного вывода пользовательских имён в Telegram Markdown.

Telegram отклоняет всё сообщение целиком, если разметка не парсится. Имя вида
`_Вася` или `Маша*` ломало предупреждения, мут-уведомления и /mystats: сообщение
не отправлялось, а нарушение при этом уже засчитывалось.
"""

import re

# Спецсимволы legacy-Markdown (parse_mode="Markdown")
_MD_SPECIALS = ("\\", "[", "]", "*", "_", "`")


def escape_md(text: str | None) -> str:
    """Экранирует спецсимволы Markdown в произвольном пользовательском тексте."""
    if not text:
        return ""
    result = str(text)
    for ch in _MD_SPECIALS:
        result = result.replace(ch, f"\\{ch}")
    return result


def user_mention(display_name: str | None, user_id: int) -> str:
    """Кликабельное упоминание пользователя с экранированным именем."""
    name = escape_md(display_name) or f"ID: {user_id}"
    return f"[{name}](tg://user?id={user_id})"


def display_username(raw_username: str | None, user_id: int | None = None) -> str:
    """
    Приводит username к единому виду для вывода: ровно один «@» у ников и
    просто имя у тех, у кого username нет (в БД оно хранится в трёх форматах).
    """
    if not raw_username:
        return f"ID: {user_id}" if user_id else "Участник"
    name = raw_username.strip()
    if name.startswith("ID:"):
        return name
    bare = name.lstrip("@")
    if not bare:
        return f"ID: {user_id}" if user_id else "Участник"
    # В старых записях ник мог лежать без «@» — восстанавливаем его по виду строки
    if re.fullmatch(r'[A-Za-z0-9_]{3,32}', bare):
        return "@" + escape_md(bare)
    return escape_md(bare)
