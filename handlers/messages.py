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

    if not text_to_check:
        return

    # Record clean message to chat history buffer for /summary command (max 200 messages)
    if text_to_check and not text_to_check.startswith("/"):
        from datetime import datetime
        now_dt = datetime.now()
        chat_hist = context.bot_data.setdefault(f"chat_history_{chat.id}", [])
        chat_hist.append({
            "name": user.full_name,
            "text": text_to_check[:300],
            "date": now_dt.strftime("%Y-%m-%d"),
            "time": now_dt.strftime("%H:%M")
        })
        if len(chat_hist) > 200:
            chat_hist.pop(0)

    # Retrieve custom bad words from database
    custom_bad_words = await database.get_bad_words(DB_PATH)

    has_violation, matched_word = find_violation(text_to_check, custom_bad_words)

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
    if message.text:
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
            and f"@{bot_username.lower()}" in message.text.lower()
        )

        if is_reply_to_bot or is_mentioned:
            import re
            prompt = message.text
            if bot_username:
                prompt = re.sub(rf'@{re.escape(bot_username)}', '', prompt, flags=re.IGNORECASE).strip()

            if prompt:
                logger.info(f"AI response requested by user {user.id} ({user.full_name}): '{prompt}'")
                try:
                    await context.bot.send_chat_action(chat_id=chat.id, action="typing")
                    ai_reply = await groq_service.generate_ai_reply(prompt, user.full_name)
                    await message.reply_text(ai_reply, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Error sending AI reply to user {user.id}: {e}")
        else:
            # Check if organic AI chat response is enabled by admin (/chattag on)
            random_tag_setting = await database.get_setting(DB_PATH, f"random_tag_{chat.id}", "0")
            if random_tag_setting == "1":
                import random, time

                # FILTER 1: If user is replying to ANOTHER user, DO NOT INTERRUPT their conversation!
                if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id != context.bot.id:
                    return

                # FILTER 2: Do NOT trigger organic response on super short questions/phrases (<= 2 words or < 8 chars) like "Каким образом?"
                words_count = len(message.text.strip().split())
                if words_count <= 2 or len(message.text.strip()) < 8:
                    return

                # Cooldown per user: Bot will NOT cling to the same user within 10 minutes (600 seconds)
                last_replied_map = context.bot_data.setdefault("user_ai_last_reply", {})
                last_replied_time = last_replied_map.get(user.id, 0)
                now_ts = time.time()

                COOLDOWN_SECONDS = 600  # 10 minutes pause per user

                if now_ts - last_replied_time >= COOLDOWN_SECONDS:
                    text_lower = message.text.lower()
                    distress_keywords = [
                        "тревог", "паник", "устал", "тяжело", "выгорел", "выгорела",
                        "плохо", "груст", "боль", "слез", "слезы", "плач", "надоело",
                        "бесит", "давлен", "приступ", "страш", "страх", "одиноко", "не могу больше"
                    ]
                    is_emotional_distress = any(kw in text_lower for kw in distress_keywords)
                    should_respond = is_emotional_distress or (random.random() < 0.20)

                    if should_respond:
                        logger.info(f"Organic AI response triggered for user {user.id} ({user.full_name}) (distress={is_emotional_distress})")
                        try:
                            await context.bot.send_chat_action(chat_id=chat.id, action="typing")
                            user_tag = f"@{user.username}" if user.username else user.full_name

                            if is_emotional_distress:
                                prompt = (
                                    f"Участник {user.full_name} ({user_tag}) написал: «{message.text}».\n"
                                    f"Оцени контекст: если {user.full_name} пишет о СОБСТВЕННОЙ тревоге/панике — поддержать коротко в 1-2 предложениях.\n"
                                    f"Если он/она просто даёт совет другому — ответь коротко в 1 предложение.\n"
                                    f"Обязательно соблюдай грамотный гендерный род по имени ({user.full_name})!\n"
                                    f"ВАЖНО: Пиши ОЧЕНЬ коротко (максимум 1-2 предложения)."
                                )
                            else:
                                prompt = (
                                    f"Участник {user.full_name} ({user_tag}) написал в чат: «{message.text}».\n"
                                    f"Органично ответь ему/ей коротко в 1 предложение и обратись {user_tag}.\n"
                                    f"Соблюдай правильный гендерный род по имени ({user.full_name})!\n"
                                    f"ВАЖНО: Пиши ОЧЕНЬ коротко (1 предложение)."
                                )

                            ai_reply = await groq_service.generate_ai_reply(prompt, user.full_name)
                            await message.reply_text(ai_reply, parse_mode="Markdown")
                            # Set cooldown timestamp so bot does NOT cling to this user for 10 minutes
                            last_replied_map[user.id] = now_ts
                        except Exception as e:
                            logger.error(f"Error sending organic AI tag reply: {e}")
