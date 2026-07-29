import logging
import re
import time
from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import DB_PATH, ADMIN_ID
import database
from conflict_detector import find_violation
import moderation
import groq_service
import text_utils
import telegram_utils

logger = logging.getLogger("PeaceMirorBot.handlers.messages")


# Безусловная благодарность — засчитывается в любом контексте
THANKS_STRONG_REGEX = re.compile(
    r'(?:^|\W)(?:спасибо|спсибо|спасиб|спс|пасиб|благодарю|благодарность|'
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

HEART_EMOJIS = ("❤️", "💖", "🤍", "💕", "💜", "💙", "🖤", "💗", "💓", "💞", "💘", "🥰")
THANKS_POINTS = 5


async def _handle_thanks(update: Update, context: ContextTypes.DEFAULT_TYPE, message, user):
    """Начисляет репутацию адресату благодарности (по реплаю или упоминанию)."""
    text = message.text or ""
    stripped = text.strip()
    is_short = len(stripped.split()) <= MAX_WEAK_THANKS_WORDS
    is_question = stripped.endswith("?")

    is_thanks = (
        bool(THANKS_STRONG_REGEX.search(text))
        or stripped == "+"
        or any(h in text for h in HEART_EMOJIS)
        or (bool(THANKS_WEAK_REGEX.search(text)) and is_short and not is_question)
    )
    if not is_thanks:
        return

    target_id = None
    target_username = None
    target_display = None

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        if target.is_bot:
            return
        target_id, target_username, target_display = target.id, target.username, target.full_name
    elif message.entities:
        for entity in message.entities:
            if entity.type == "text_mention" and entity.user:
                target_id = entity.user.id
                target_username = entity.user.username
                target_display = entity.user.full_name
                break
            if entity.type == "mention":
                uname = text[entity.offset:entity.offset + entity.length].strip()
                # Поиск учитывает оба формата хранения ника: раньше запрос шёл без
                # "@", а в БД ник лежит с "@", и карма по упоминанию не работала
                found = await database.find_user_by_name_or_alias(DB_PATH, update.effective_chat.id, uname)
                if found:
                    target_id, target_username = found[0], found[1]
                    target_display = found[1]
                    break

    if not target_id or target_id == user.id or target_id == context.bot.id:
        return

    new_pts = await database.add_peace_points(DB_PATH, target_id, target_username, THANKS_POINTS)
    badge, title = database.get_rank_title(new_pts)
    sender_mention = text_utils.user_mention(user.full_name, user.id)
    target_mention = text_utils.user_mention(target_display, target_id)
    try:
        await message.reply_text(
            f"🕊️ {sender_mention} дарит тепло и респект! {target_mention} получает "
            f"**+{THANKS_POINTS} к Репутации** (Всего репутации: **{new_pts}** {badge} {title}).",
            parse_mode="Markdown"
        )
    except TelegramError as e:
        logger.warning(f"Failed to send thanks message in chat {update.effective_chat.id}: {e}")


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Evaluates incoming group chat text messages and voice messages (ГС) for profanity or insults.
    Модерация применяется ко всем участникам, включая админов: сообщение админа
    тоже удаляется, но мут Telegram к администратору применить не даёт.
    """
    message = update.effective_message
    if not message:
        return

    # Skip messages sent by bots
    if message.from_user and message.from_user.is_bot:
        return

    user = message.from_user
    chat = update.effective_chat

    # У сообщений от имени канала from_user отсутствует — дальше код обращается
    # к user.full_name / user.id, и обработчик падал с AttributeError
    if not user or not chat:
        return

    u_str = f"@{user.username}" if user.username else user.full_name
    await database.ensure_user_exists(DB_PATH, user.id, u_str)

    # Счётчик для строки HEARTBEAT в логе
    context.bot_data["messages_processed"] = context.bot_data.get("messages_processed", 0) + 1

    # Track last activity timestamp for 7-hour inactivity check (eyes emoji 👀)
    context.bot_data[f"last_msg_time_{chat.id}"] = time.time()
    context.bot_data[f"eyes_sent_{chat.id}"] = False
    active_chats = context.bot_data.setdefault("active_chats", set())
    active_chats.add(chat.id)

    text_to_check = message.text

    # Handle Voice Messages (Голосовые сообщения / ГС)
    if message.voice:
        try:
            voice_file = await context.bot.get_file(message.voice.file_id)
            file_bytearray = await voice_file.download_as_bytearray()
            transcribed = await groq_service.transcribe_voice(bytes(file_bytearray))
            if transcribed:
                text_to_check = transcribed
                logger.info(f"Groq Whisper transcribed voice message from user {user.id} ({user.full_name}): '{transcribed}'")
        except Exception as e:
            logger.error(f"Error processing voice message for user {user.id}: {e}")
    elif message.sticker:
        emoji_str = f" {message.sticker.emoji}" if message.sticker and message.sticker.emoji else ""
        text_to_check = f"[Стикер{emoji_str}]"
    elif (message.photo or message.animation) and message.caption:
        text_to_check = message.caption
    elif (message.photo or message.animation) and not text_to_check:
        text_to_check = "[Картинка/GIF]"

    if not text_to_check:
        return

    # Record clean message to persistent SQLite database for /summary command
    if text_to_check and not text_to_check.startswith("/"):
        await database.save_chat_message(DB_PATH, chat.id, user.full_name, text_to_check[:300])

    # Retrieve custom bad words and custom allowed words from database
    # Стоп-лист и белый список — свои у каждого чата
    custom_bad_words = await database.get_bad_words(DB_PATH, chat.id)
    custom_allowed_words = await database.get_allowed_words(DB_PATH, chat.id)

    has_violation, matched_word = find_violation(text_to_check, custom_bad_words, custom_allowed_words)

    if has_violation and matched_word:
        logger.info(f"Conflict/profanity detected in message/voice from user {message.from_user.id}: matched '{matched_word}'")
        await database.add_peace_points(DB_PATH, user.id, user.username, -15)
        await moderation.process_violation(update, context, matched_word)
        return

    # User wrote a clean message — add +1 peace point
    await database.add_peace_points(DB_PATH, user.id, user.username, 1)

    if message.text:
        await _handle_thanks(update, context, message, user)

    # If no profanity violation, check if user explicitly called/addressed the bot
    if text_to_check:
        bot_username = context.bot_data.get("bot_username")
        if not bot_username:
            try:
                me = await context.bot.get_me()
                bot_username = me.username
                context.bot_data["bot_username"] = bot_username
            except Exception:
                bot_username = context.bot.username or ""

        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == context.bot.id
        )
        is_mentioned = (
            bot_username
            and f"@{bot_username.lower()}" in text_to_check.lower()
        )
        is_name_called = "мирчик" in text_to_check.lower()

        if is_reply_to_bot or is_mentioned or is_name_called:
            user_text = text_to_check
            if bot_username:
                user_text = re.sub(rf'@{re.escape(bot_username)}', '', user_text, flags=re.IGNORECASE).strip()

            target_msg = message.reply_to_message or message
            media_bytes, media_type = await telegram_utils.get_message_media_bytes(context, target_msg)

            u_tag = f"@{user.username}" if user.username else text_utils.user_mention(user.full_name, user.id)

            if media_bytes:
                await telegram_utils.send_chat_action_safe(context, chat.id, "typing")
                try:
                    vision_reply = await groq_service.analyze_image(media_bytes, user_text, user.full_name)
                    if vision_reply:
                        full_reply = f"{u_tag}, {vision_reply}"
                        try:
                            await message.reply_text(full_reply, parse_mode="Markdown")
                        except Exception:
                            await message.reply_text(full_reply)
                        return
                except Exception as e:
                    logger.error(f"Error in Vision processing ({media_type}) for group message: {e}")

            if message.reply_to_message:
                replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
                if not replied_text:
                    if message.reply_to_message.sticker:
                        stk_emoji = message.reply_to_message.sticker.emoji or ""
                        replied_text = f"[Стикер {stk_emoji}]"
                    elif message.reply_to_message.animation:
                        replied_text = "[GIF-анимация]"
                    else:
                        replied_text = "[Медиасообщение]"

                replied_author = message.reply_to_message.from_user.full_name if message.reply_to_message.from_user else "Участник"
                if user_text:
                    prompt = f"Контекст (сообщение от {replied_author}): «{replied_text}»\nВопрос/ответ пользователя: {user_text}"
                else:
                    prompt = f"Прокомментируй сообщение от {replied_author}: «{replied_text}»"
            else:
                prompt = user_text

            if prompt:
                logger.info(f"AI response requested by user {user.id} ({user.full_name}): '{prompt}'")
                await telegram_utils.send_chat_action_safe(context, chat.id, "typing")
                try:
                    ai_reply = await groq_service.generate_ai_reply(prompt, user.full_name)
                    full_reply = f"{u_tag}, {ai_reply}"
                    try:
                        await message.reply_text(full_reply, parse_mode="Markdown")
                    except Exception:
                        await message.reply_text(full_reply)
                except Exception as e:
                    logger.error(f"Error sending AI reply to user {user.id}: {e}")
                    try:
                        await message.reply_text("😴 Ой, Мирчик сейчас немного занят или отвлёкся на чай! Напиши мне через пару секунд ☕")
                    except Exception:
                        pass
