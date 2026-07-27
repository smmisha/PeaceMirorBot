import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, ChatMember
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import ADMIN_ID, DB_PATH
from moderation import MUTED_PERMISSIONS, UNMUTED_PERMISSIONS
import database
import notification

logger = logging.getLogger("PeaceMirorBot.handlers.admin")


async def _track_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Records admin activity for smart notification routing."""
    user = update.effective_user
    chat = update.effective_chat
    if user and chat and chat.type != "private":
        username = f"@{user.username}" if user.username else user.full_name
        await database.record_admin_activity(DB_PATH, user.id, chat.id, username)
        # Mark any pending notifications as resolved (admin is responding)
        notification.mark_notification_resolved(f"chat_{chat.id}")


import re


async def _resolve_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Resolves the target user for an admin command from:
    1. Reply to user message.
    2. Reply to bot notification message (extracts mentioned user from entities/link).
    3. Command arguments (@username or user_id).
    Returns (user_id, username, mention_str).
    """
    message = update.effective_message

    # Option 1 & 2: Reply to message
    if message and message.reply_to_message:
        reply_msg = message.reply_to_message
        u = reply_msg.from_user

        # Option 1: Reply directly to a regular user message
        if u and not u.is_bot:
            username = f"@{u.username}" if u.username else u.full_name
            mention = f"[{u.full_name}](tg://user?id={u.id})"
            return u.id, username, mention

        # Option 2: Reply to a BOT notification message (e.g. "[Удалённое сообщение]" or mute notice)
        if reply_msg.entities or reply_msg.text:
            # Check text_mention entities first
            if reply_msg.entities:
                for entity in reply_msg.entities:
                    if entity.type == "text_mention" and entity.user:
                        target = entity.user
                        username = f"@{target.username}" if target.username else target.full_name
                        mention = f"[{target.full_name}](tg://user?id={target.id})"
                        return target.id, username, mention

            # Check tg://user?id= regex in message text
            msg_text = reply_msg.text or reply_msg.caption or ""
            match = re.search(r'tg://user\?id=(\d+)', msg_text)
            if match:
                target_id = int(match.group(1))
                stats = await database.get_user_stats(DB_PATH, target_id)
                username = stats.get("username") or f"ID: {target_id}"
                mention = f"[{username}](tg://user?id={target_id})"
                return target_id, username, mention

    # Option 3: Command arguments (@username or user_id)
    if context.args:
        for arg in context.args:
            raw = arg.strip()
            if raw.isdigit():
                user_id = int(raw)
                stats = await database.get_user_stats(DB_PATH, user_id)
                username = stats.get("username") or f"ID: {user_id}"
                mention = f"[{username}](tg://user?id={user_id})"
                return user_id, username, mention
            elif raw.startswith("@"):
                target_username = raw[1:].lower()
                async with database.aiosqlite.connect(DB_PATH) as db:
                    async with db.execute(
                        "SELECT user_id, username FROM users WHERE LOWER(username) = ?",
                        (f"@{target_username}",)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            return row[0], row[1], f"[{row[1]}](tg://user?id={row[0]})"

                    async with db.execute(
                        "SELECT user_id, username FROM mutes WHERE LOWER(username) = ?",
                        (f"@{target_username}",)
                    ) as cursor:
                        row = await cursor.fetchone()
                        if row:
                            return row[0], row[1], f"[{row[1]}](tg://user?id={row[0]})"

                return None, raw, raw

    return None, None, None


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if the user executing the command is a chat admin or global bot admin."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False

    if ADMIN_ID and user.id == ADMIN_ID:
        return True

    if chat.type == "private":
        return True

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except TelegramError as e:
        logger.error(f"Error checking admin status for user {user.id}: {e}")
        return False


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /mute [minutes] [@username | user_id] [reason] (or as reply)."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора для использования этой команды.")
        return

    await _track_admin(update, context)

    user_id, username, mention = await _resolve_target_user(update, context)
    if not user_id:
        if username and username.startswith("@"):
            await update.message.reply_text(
                f"❌ Пользователь `{username}` не найден в базе данных бота.\n"
                f"Выдайте мут ответом на его сообщение или укажите его Telegram ID.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "⚠️ Укажите пользователя ответом на сообщение или укажите `@username` / `ID`:\n"
                "Пример: `/mute 15 @username Причина`",
                parse_mode="Markdown"
            )
        return

    minutes = 15
    reason_parts = []
    if context.args:
        for arg in context.args:
            if arg.startswith("@") or (arg.isdigit() and int(arg) == user_id):
                continue
            elif arg.isdigit():
                minutes = int(arg)
            else:
                reason_parts.append(arg)

    reason = " ".join(reason_parts) if reason_parts else "Нарушение правил общения"
    until_date = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    chat = update.effective_chat

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=MUTED_PERMISSIONS,
            until_date=until_date
        )
        await database.set_user_mute(DB_PATH, chat.id, user_id, username, until_date)

        from moderation import format_mute_duration
        await update.effective_message.reply_text(
            f"🤐 {mention} переведён администратором в режим чтения на **{format_mute_duration(minutes)}**.\n"
            f"📌 *Причина:* {reason}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to mute user {user_id}: {e}")
        await update.effective_message.reply_text(f"❌ Не удалось выдать мут: {e}")


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /unmute [@username | user_id] (or as reply to a user message)."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    user_id, username, mention = await _resolve_target_user(update, context)
    if not user_id:
        if username and username.startswith("@"):
            await update.message.reply_text(
                f"❌ Пользователь `{username}` не найден в базе данных бота.\n"
                f"Снимите мут ответом на его сообщение или укажите его Telegram ID.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "⚠️ Укажите пользователя ответом на сообщение или укажите `@username` / `ID`:\n"
                "Пример: `/unmute @username` или `/unmute 12345678`",
                parse_mode="Markdown"
            )
        return

    chat = update.effective_chat
    try:
        # Clear punishment mute in DB
        await database.clear_user_mute(DB_PATH, chat.id, user_id)

        # Trigger captcha verification for user
        from handlers.captcha import trigger_captcha_for_user
        await trigger_captcha_for_user(context, chat, user_id, username)

        await update.effective_message.reply_text(
            f"🟢 Наказание с пользователя {mention} снято администратором!\n"
            f"Для завершения разблокировки пользователю нужно подтвердить капчу.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to unmute user {user_id}: {e}")
        await update.effective_message.reply_text(f"❌ Не удалось снять мут: {e}")


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /warn [@username | user_id] [reason] (or as reply)."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    user_id, username, mention = await _resolve_target_user(update, context)
    if not user_id:
        if username and username.startswith("@"):
            await update.message.reply_text(
                f"❌ Пользователь `{username}` не найден в базе данных бота.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "⚠️ Укажите пользователя ответом на сообщение или укажите `@username` / `ID`:\n"
                "Пример: `/warn @username Причина`",
                parse_mode="Markdown"
            )
        return

    reason_parts = [arg for arg in context.args if not arg.startswith("@") and not (arg.isdigit() and int(arg) == user_id)] if context.args else []
    reason = " ".join(reason_parts) if reason_parts else "Административное предупреждение"

    new_count = await database.record_violation(DB_PATH, user_id, username)

    await update.effective_message.reply_text(
        f"⚠️ {mention} получил предупреждение от администратора!\n"
        f"📌 *Причина:* {reason}\n"
        f"📊 *Всего предупреждений:* `{new_count}`",
        parse_mode="Markdown"
    )


async def cmd_addword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /addword <word>."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    if not context.args:
        await update.message.reply_text("⚠️ Укажите слово или фразу: `/addword <слово>`", parse_mode="Markdown")
        return

    word = " ".join(context.args).strip().lower()
    success = await database.add_bad_word(DB_PATH, word)
    if success:
        await update.message.reply_text(f"✅ Слово `{word}` успешно добавлено в стоп-лист.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Слово `{word}` уже есть в стоп-листе.", parse_mode="Markdown")


async def cmd_removeword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /removeword <word>."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    if not context.args:
        await update.message.reply_text("⚠️ Укажите слово: `/removeword <слово>`", parse_mode="Markdown")
        return

    word = " ".join(context.args).strip().lower()
    success = await database.remove_bad_word(DB_PATH, word)
    if success:
        await update.message.reply_text(f"✅ Слово `{word}` удалено из стоп-листа.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Слово `{word}` не найдено в стоп-листе.", parse_mode="Markdown")


async def cmd_wordlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /wordlist."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    words = await database.get_bad_words(DB_PATH)
    if not words:
        await update.message.reply_text("📋 Стоп-лист кастомных слов пуст.")
        return

    words_formatted = "\n".join(f"• `{w}`" for w in words)
    await update.message.reply_text(f"📋 **Кастомный стоп-лист слов ({len(words)}):**\n\n{words_formatted}", parse_mode="Markdown")


async def cmd_resetstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /resetstats [@username | user_id | all] (or as reply)."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    # Check if admin requested to reset ALL users at once (/resetstats all or /resetstats все)
    if context.args and context.args[0].strip().lower() in ("all", "все", "*"):
        await database.reset_all_users_stats(DB_PATH)
        await update.effective_message.reply_text("🔄 **Статистика нарушений и все предупреждения для ВСЕХ пользователей чата успешно сброшены!**", parse_mode="Markdown")
        return

    user_id, username, mention = await _resolve_target_user(update, context)
    if not user_id:
        if username and username.startswith("@"):
            await update.message.reply_text(
                f"❌ Пользователь `{username}` не найден в базе данных бота.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "⚠️ Укажите пользователя ответом на сообщение, указав `@username` / `ID`, или сбросьте всех:\n"
                "Пример: `/resetstats @username` или `/resetstats all`",
                parse_mode="Markdown"
            )
        return

    await database.reset_user_stats(DB_PATH, user_id)
    await update.effective_message.reply_text(f"✅ Статистика нарушений для {mention} полностью сброшена.", parse_mode="Markdown")


async def cmd_chattag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /chattag [on | off] to toggle random organic AI chat tags."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    arg = context.args[0].strip().lower() if context.args else ""
    setting_key = f"random_tag_{update.effective_chat.id}"
    current_val = await database.get_setting(DB_PATH, setting_key, "0")

    if arg in ("on", "1", "вкл", "включить"):
        if current_val == "1":
            await update.effective_message.reply_text(
                "ℹ️ **Режим ИИ по наитию и душевной поддержки УЖЕ ВКЛЮЧЕН!**\n"
                "Бот уже активен в чате и поддерживает участников.",
                parse_mode="Markdown"
            )
        else:
            await database.set_setting(DB_PATH, setting_key, "1")
            await update.effective_message.reply_text(
                "🟢 **Режим ИИ по наитию и душевной поддержки ВКЛЮЧЕН!**\n"
                "• На сообщения про тревогу/панику/усталость бот теперь отвечает на 100% со словами поддержки.\n"
                "• На обычные сообщения вклинивается спонтанно по настроению ИИ.",
                parse_mode="Markdown"
            )
    elif arg in ("off", "0", "выкл", "выключить"):
        if current_val == "0":
            await update.effective_message.reply_text(
                "ℹ️ **Режим ИИ по наитию УЖЕ ВЫКЛЮЧЕН.**\n"
                "Бот отвечает строго только при прямом вызове (/ai, реплай, @тег).",
                parse_mode="Markdown"
            )
        else:
            await database.set_setting(DB_PATH, setting_key, "0")
            await update.effective_message.reply_text(
                "🛑 **Режим ИИ по наитию ВЫКЛЮЧЕН!**\n"
                "Бот будет отвечать строго только при прямом вызове (/ai, реплай или @упоминание).",
                parse_mode="Markdown"
            )
    else:
        status_text = "🟢 **ВКЛЮЧЕНО**" if current_val == "1" else "🔴 **ВЫКЛЮЧЕНО**"
        await update.effective_message.reply_text(
            f"🎲 **Текущий статус ИИ по наитию и поддержки:** {status_text}\n\n"
            f"• Включить: `/chattag on`\n"
            f"• Выключить: `/chattag off`",
            parse_mode="Markdown"
        )


async def cmd_uncaptcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /uncaptcha [@username | ID | reply] to manually approve/remove captcha for a user."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    user_id, username, mention = await _resolve_target_user(update, context)
    if not user_id:
        await update.message.reply_text(
            "⚠️ Укажите пользователя ответом на сообщение или `@username` / `ID`:\n"
            "Пример: `/uncaptcha @username`",
            parse_mode="Markdown"
        )
        return

    from handlers.captcha import approve_user_captcha
    ok = await approve_user_captcha(context, update.effective_chat.id, user_id)
    if ok:
        await update.effective_message.reply_text(
            f"✅ **Капча у пользователя {mention} успешно снята!** Права отправки сообщений восстановлены.",
            parse_mode="Markdown"
        )
    else:
        await update.effective_message.reply_text("❌ Не удалось снять капчу.", parse_mode="Markdown")
