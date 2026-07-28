# Задание: две правки в PeaceMirorBot

Выполни обе задачи ниже. Не делай ничего сверх описанного.

## Контекст проекта

Telegram-бот-модератор на `python-telegram-bot` v20+ (async), Python 3.11+, SQLite через
`aiosqlite`. Точка входа — `bot.py`. Структура плоская: `moderation.py`, `database.py`,
`conflict_detector.py`, `telegram_utils.py`, `text_utils.py`, хендлеры в `handlers/`.

Бот работает на **бесплатном PythonAnywhere**, где весь исходящий трафик идёт через
`proxy.server:3128`. Этот прокси периодически отдаёт `503 Service Unavailable`.
PTB заворачивает такую ошибку в `telegram.error.NetworkError` — это видно в логе:

```
telegram.error.NetworkError: httpx.ProxyError: 503 Service Unavailable
```

Это причина задачи №2.

---

## Задача 1. Ложные начисления репутации

### Проблема

В `handlers/messages.py` есть `THANKS_REGEX` — список слов-благодарностей, за которые
собеседник получает +5 к репутации. В нём стоят слова `принял` и `принято`, которые в
живой речи чаще всего обычные глаголы. Реальный случай из чата:

> Irene (ответом на сообщение «Темный рыцарь»): **«Ну ты же принял и лечился так?»**
> → бот начислил Темному рыцарю +5 к репутации

Границы слова тут не помогают — нужен контекст.

### Что сделать

**1.1.** В `handlers/messages.py` заменить единственный `THANKS_REGEX` на два регекса и
константу:

```python
# Безусловная благодарность — засчитывается в любом контексте
THANKS_STRONG_REGEX = re.compile(
    r'(?:^|\W)(?:спасибо|спсибо|спасиб|пасиб|благодарю|благодарность|'
    r'респект|сенкс|плюсую|\+1|\+\+|/thanks)(?:\W|$)',
    re.IGNORECASE
)

# Похвала и короткие отклики — только в коротком утвердительном сообщении.
# Иначе «Ну ты же принял и лечился так?» читается как благодарность.
THANKS_WEAK_REGEX = re.compile(
    r'(?:^|\W)(?:красавчик|красава|молодец|молодчина|обнял|обняла|'
    r'принял|принято)(?:\W|$)',
    re.IGNORECASE
)

MAX_WEAK_THANKS_WORDS = 4
```

**1.2.** В функции `_handle_thanks` заменить вычисление `is_thanks` на:

```python
    stripped = text.strip()
    is_short = len(stripped.split()) <= MAX_WEAK_THANKS_WORDS
    is_question = stripped.endswith("?")

    is_thanks = (
        bool(THANKS_STRONG_REGEX.search(text))
        or stripped == "+"
        or any(h in text for h in HEART_EMOJIS)
        or (bool(THANKS_WEAK_REGEX.search(text)) and is_short and not is_question)
    )
```

Остальное тело `_handle_thanks` (поиск получателя, начисление, ответ) не трогать.

**1.3.** В том же сообщении о начислении заменить конструкцию со скобками
`выразил(а) тепло и респект` на безродовую формулировку: `дарит тепло и респект`.
В проекте действует правило: никаких «сделал(а)», «один(на)» и подобных скобок.

### Проверка

Создай `test_thanks.py` и добейся, чтобы он проходил:

```python
import unittest
from handlers.messages import THANKS_STRONG_REGEX, THANKS_WEAK_REGEX, MAX_WEAK_THANKS_WORDS


def is_thanks(text: str) -> bool:
    stripped = text.strip()
    is_short = len(stripped.split()) <= MAX_WEAK_THANKS_WORDS
    is_question = stripped.endswith("?")
    return (
        bool(THANKS_STRONG_REGEX.search(text))
        or stripped == "+"
        or (bool(THANKS_WEAK_REGEX.search(text)) and is_short and not is_question)
    )


class TestThanks(unittest.TestCase):
    def test_real_gratitude_counts(self):
        for t in ["Спасибо большое!", "спс", "респект тебе", "Молодец!",
                  "принято", "+1", "плюсую", "+"]:
            self.assertTrue(is_thanks(t), f"должно засчитаться: {t}")

    def test_ordinary_speech_does_not_count(self):
        for t in ["Ну ты же принял и лечился так?",
                  "принял таблетки утром и вечером",
                  "я не понял вопрос совсем",
                  "он молодец конечно но решение принял странное",
                  "принял?"]:
            self.assertFalse(is_thanks(t), f"НЕ должно засчитаться: {t}")


if __name__ == "__main__":
    unittest.main()
```

---

## Задача 2. Ретраи при сбое прокси (503)

### Проблема

Одна сетевая ошибка теряет действие модерации целиком. Реальный случай из лога:

```
19:52:08  Conflict/profanity detected from user 1039059036: matched 'ебать'
19:52:09  User @wizit228 violation #1. Triggered by: 'ебать'
19:52:09  WARNING - Markdown warning failed: httpx.ProxyError: 503. Sending plain text.
19:52:09  ERROR - Unhandled exception: httpx.ProxyError: 503 Service Unavailable
```

Нарушение засчитано, а предупреждение в чат не ушло: обе попытки отправки упёрлись в 503.
По той же схеме может не примениться `restrict_chat_member` — тогда база запишет мут,
которого в Telegram нет.

Прокси флапает секундами, поэтому повторная попытка почти всегда проходит.

### Что сделать

**2.1.** В `telegram_utils.py` добавить импорты и хелпер:

```python
import asyncio
from telegram.error import NetworkError, TimedOut, RetryAfter

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
```

`Forbidden` и `BadRequest` (нет прав, юзер не найден, битая разметка) хелпер намеренно
НЕ ловит — повторять их бессмысленно, они должны дойти до существующих `except`.

**2.2.** В `moderation.py` добавить `import telegram_utils` и применить хелпер в
`process_violation` в трёх местах.

Удаление сообщения — `await message.delete()` заменить на:

```python
            await telegram_utils.with_retry(lambda: message.delete(), "удаление сообщения")
```
Существующий `except (Forbidden, BadRequest)` оставить без изменений.

Мут — заменить блок с `restrict_chat_member` на:

```python
        muted = False
        try:
            result = await telegram_utils.with_retry(
                lambda: context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user_id,
                    permissions=MUTED_PERMISSIONS,
                    until_date=until_date
                ),
                f"мут пользователя {user_id}"
            )
            muted = result is not None
            if muted:
                await database.set_user_mute(
                    DB_PATH, chat_id=chat.id, user_id=user_id,
                    username=username, muted_until=until_date
                )
        except (Forbidden, BadRequest) as e:
            logger.error(f"Could not restrict user {user_id}: {e}")
            await notify_admin_missing_rights(context, "ограничение участника (мут)", chat.id, chat_title)
```

Важно: запись в базу теперь только при успешном муте — иначе база и Telegram расходятся.

Отправка уведомлений — оба места (предупреждение на шаге 1 и итоговое сообщение о муте)
привести к шаблону:

```python
        try:
            await telegram_utils.with_retry(
                lambda: message.reply_text(text, parse_mode="Markdown"),
                "предупреждение о нарушении"
            )
        except TelegramError as e:
            logger.warning(f"Markdown failed: {e}. Sending plain text.")
            await telegram_utils.with_retry(
                lambda: message.reply_text(text.replace("**", "").replace("*", "")),
                "предупреждение (обычный текст)"
            )
```

### Проверка

Создай `test_retry.py` и добейся, чтобы он проходил:

```python
import asyncio
import unittest
from telegram.error import NetworkError, BadRequest
import telegram_utils


class TestWithRetry(unittest.TestCase):
    def test_recovers_after_network_failures(self):
        telegram_utils.RETRY_DELAY = 0  # без пауз в тесте
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise NetworkError("httpx.ProxyError: 503 Service Unavailable")
            return "отправлено"

        self.assertEqual(asyncio.run(telegram_utils.with_retry(flaky, "тест")), "отправлено")
        self.assertEqual(calls["n"], 3)

    def test_gives_up_and_returns_none(self):
        telegram_utils.RETRY_DELAY = 0

        async def always_down():
            raise NetworkError("503")

        self.assertIsNone(asyncio.run(telegram_utils.with_retry(always_down, "тест")))

    def test_does_not_retry_permanent_errors(self):
        telegram_utils.RETRY_DELAY = 0
        calls = {"n": 0}

        async def forbidden():
            calls["n"] += 1
            raise BadRequest("user not found")

        with self.assertRaises(BadRequest):
            asyncio.run(telegram_utils.with_retry(forbidden, "тест"))
        self.assertEqual(calls["n"], 1, "постоянные ошибки повторять нельзя")


if __name__ == "__main__":
    unittest.main()
```

---

## Чего делать НЕЛЬЗЯ

- Не трогать `conflict_detector.py`. Регексы там выверены: корни мата ищутся только
  с начала слова или после приставки, иначе бот банит «хлеба», «требую», «волшебный»,
  «Херсон», «парикмахер». Любая правка там ломает 9 существующих тестов.
- Не менять лестницу наказаний в `process_violation` (1-е нарушение — только
  предупреждение без удаления; удаление и мут начинаются со второго).
- Не увеличивать `NETWORK_RETRIES` выше 3: суммарная задержка вырастет и апдейты
  начнут копиться в очереди.
- Не оборачивать в ретраи `send_chat_action` — индикатор «печатает…» необязателен,
  для него уже есть `send_chat_action_safe`.
- Не рефакторить ничего постороннего, не переименовывать функции, не менять формат логов
  (строка `HEARTBEAT` используется для мониторинга).
- Не коммитить и не пушить без отдельной просьбы владельца.
- Не запускать бота: он работает на сервере, второй экземпляр вызовет
  `409 Conflict: terminated by other getUpdates request`.

## Финальная проверка

Все тесты проекта должны проходить:

```bash
python -m unittest test_conflict_detector test_wordlists test_thanks test_retry
```

И модули должны импортироваться без ошибок:

```bash
python -c "import bot, moderation, telegram_utils; from handlers import messages; print('OK')"
```

Деплой владелец делает сам: `git pull` на сервере и перезапуск процесса.
