import logging
from telegram import Update, ChatMember
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import DB_PATH, ADMIN_ID
import database
from conflict_detector import find_violation
import moderation
import groq_service

logger = logging.getLogger("PeaceMirorBot.handlers.messages")


async def _is_chat_admin(context, chat_id: int, user_id: int) -> bool:
    """Checks if a user is an admin/owner in the chat. Returns False on errors."""
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except TelegramError:
        return False


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Evaluates incoming group chat text messages and voice messages (ГС) for profanity or insults.
    Chat admins are exempt from automatic moderation.
    """
    message = update.effective_message
    if not message:
        return

    # Skip messages sent by bots
    if message.from_user and message.from_user.is_bot:
        return

    user = message.from_user
    chat = update.effective_chat

    if user:
        u_str = f"@{user.username}" if user.username else user.full_name
        await database.ensure_user_exists(DB_PATH, user.id, u_str)

    import time
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
    custom_bad_words = await database.get_bad_words(DB_PATH)
    custom_allowed_words = await database.get_allowed_words(DB_PATH)

    has_violation, matched_word = find_violation(text_to_check, custom_bad_words, custom_allowed_words)

    if has_violation and matched_word:
        logger.info(f"Conflict/profanity detected in message/voice from user {message.from_user.id}: matched '{matched_word}'")
        await database.add_peace_points(DB_PATH, user.id, user.username, -15)
        await moderation.process_violation(update, context, matched_word)
        return

    # User wrote a clean message — add +1 peace point
    await database.add_peace_points(DB_PATH, user.id, user.username, 1)

    # Check for thank-you / warmth / karma transfer (via reply OR mention)
    thanks_triggers = [
        "спасибо", "спсибо", "благодарю", "респект", "+1", "+", "плюс",
        "красавчик", "молодец", "понял", "поняла", "обнял", "обняла",
        "принял", "принято", "/thanks"
    ]
    heart_emojis = ["❤️", "💖", "🤍", "💕", "💜", "💙", "🖤", "💗", "💓", "💞", "💘", "🥰"]

    if message.text:
        text_lower = message.text.strip().lower()
        has_heart = any(h in message.text for h in heart_emojis)
        is_thanks = any(kw in text_lower for kw in thanks_triggers) or has_heart

        if is_thanks:
            target_user = None
            if message.reply_to_message and message.reply_to_message.from_user:
                target_user = message.reply_to_message.from_user
            elif message.entities:
                for entity in message.entities:
                    if entity.type == "mention":
                        uname = message.text[entity.offset:entity.offset + entity.length].replace("@", "").strip()
                        if uname:
                            import aiosqlite
                            async with aiosqlite.connect(DB_PATH) as db:
                                db.row_factory = aiosqlite.Row
                                async with db.execute("SELECT user_id, username FROM users WHERE LOWER(username) = ?", (uname.lower(),)) as cursor:
                                    row = await cursor.fetchone()
                                    if row:
                                        target_user = type("UserObj", (), {"id": row["user_id"], "username": row["username"], "full_name": f"@{row['username']}", "is_bot": False})
                    elif entity.type == "text_mention" and entity.user:
                        target_user = entity.user

            if target_user and not getattr(target_user, "is_bot", False) and target_user.id != user.id:
                pts_to_add = 5
                new_pts = await database.add_peace_points(DB_PATH, target_user.id, getattr(target_user, "username", None), pts_to_add)
                badge, title = database.get_rank_title(new_pts)
                sender_mention = f"[{user.full_name}](tg://user?id={user.id})"
                target_name = getattr(target_user, "full_name", f"ID {target_user.id}")
                try:
                    await message.reply_text(
                        f"🕊️ {sender_mention} выразил(а) тепло и респект! {target_name} получает **+{pts_to_add} к Репутации** (Всего репутации: **{new_pts}** {badge} {title}).",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

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
            import re
            user_text = text_to_check
            if bot_username:
                user_text = re.sub(rf'@{re.escape(bot_username)}', '', user_text, flags=re.IGNORECASE).strip()

            if message.reply_to_message:
                replied_text = message.reply_to_message.text or message.reply_to_message.caption or ""
                if not replied_text:
                    if message.reply_to_message.photo:
                        replied_text = "[Фотография/Картинка]"
                    elif message.reply_to_message.sticker:
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
                try:
                    await context.bot.send_chat_action(chat_id=chat.id, action="typing")
                    u_tag = f"@{user.username}" if user.username else f"[{user.full_name}](tg://user?id={user.id})"
                    ai_reply = await groq_service.generate_ai_reply(prompt, user.full_name, user_mention=u_tag)
                    try:
                        await message.reply_text(ai_reply, parse_mode="Markdown")
                    except Exception:
                        # Fallback to plain text if Telegram Markdown parsing fails
                        await message.reply_text(ai_reply)
                except Exception as e:
                    logger.error(f"Error sending AI reply to user {user.id}: {e}")
                    try:
                        await message.reply_text("😴 Ой, Мирчик сейчас немного занят или отвлёкся на чай! Напиши мне через пару секунд ☕")
                    except Exception:
                        pass
