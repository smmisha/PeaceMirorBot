import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, ChatMember
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import ADMIN_ID, DB_PATH
from moderation import MUTED_PERMISSIONS, get_default_permissions
import database
import notification
import text_utils

logger = logging.getLogger("PeaceMirorBot.handlers.admin")

# Минимальный правдоподобный Telegram user_id — всё, что меньше, считаем длительностью
MIN_TELEGRAM_USER_ID = 10000


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
    1. Direct text_mention entity in the command message (e.g. tagging a user without @username via Telegram dropdown).
    2. Reply to user message or reply to bot notification message.
    3. Command arguments (@username, nickname, or user_id).
    Returns (user_id, username, mention_str).
    """
    message = update.effective_message
    if not message:
        return None, None, None

    # Priority 1: Check if command message itself has a text_mention entity (e.g. user selected from Telegram dropdown like "Анна")
    if message.entities:
        for entity in message.entities:
            if entity.type == "text_mention" and entity.user:
                target = entity.user
                username = f"@{target.username}" if target.username else target.full_name
                return target.id, username, text_utils.user_mention(target.full_name, target.id)

    # Priority 2: Reply to user message or reply to bot notification message
    if message.reply_to_message:
        reply_msg = message.reply_to_message
        u = reply_msg.from_user

        # Option A: Reply directly to a regular user message
        if u and not u.is_bot:
            username = f"@{u.username}" if u.username else u.full_name
            return u.id, username, text_utils.user_mention(u.full_name, u.id)

        # Option B: Reply to a BOT notification message (e.g. "[Удалённое сообщение]" or mute notice)
        if reply_msg.entities or reply_msg.text:
            if reply_msg.entities:
                for entity in reply_msg.entities:
                    if entity.type == "text_mention" and entity.user:
                        target = entity.user
                        username = f"@{target.username}" if target.username else target.full_name
                        return target.id, username, text_utils.user_mention(target.full_name, target.id)

            msg_text = reply_msg.text or reply_msg.caption or ""
            match = re.search(r'tg://user\?id=(\d+)', msg_text)
            if match:
                target_id = int(match.group(1))
                stats = await database.get_user_stats(DB_PATH, target_id)
                username = stats.get("username") or f"ID: {target_id}"
                return target_id, username, text_utils.user_mention(username, target_id)

    # Priority 3: Command arguments (@username, nickname, or user_id)
    if context.args:
        chat = update.effective_chat
        numeric_args: list[int] = []
        unresolved: str | None = None

        for arg in context.args:
            raw = arg.strip()
            if not raw:
                continue

            # Числа откладываем: в `/mute 15 @username` первый аргумент — это минуты,
            # а не Telegram ID. Раньше резолвер возвращал user_id=15 и мут улетал в пустоту.
            if raw.isdigit():
                numeric_args.append(int(raw))
                continue

            found = await database.find_user_by_name_or_alias(DB_PATH, chat.id if chat else 0, raw)
            if found:
                u_id, u_name = found
                return u_id, u_name, text_utils.user_mention(u_name, u_id)

            if unresolved is None:
                unresolved = raw

        # Числовым аргументом считаем Telegram ID только если он правдоподобен:
        # реальные ID — это минимум 5 знаков, а длительность мута задают минутами.
        for num in numeric_args:
            if num >= MIN_TELEGRAM_USER_ID:
                stats = await database.get_user_stats(DB_PATH, num)
                username = stats.get("username") or f"ID: {num}"
                return num, username, text_utils.user_mention(username, num)

        if unresolved:
            return None, unresolved, f"`{text_utils.escape_md(unresolved)}`"

    return None, None, None


async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Checks if the user executing the command is a chat admin or global bot admin."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False

    if ADMIN_ID and user.id == ADMIN_ID:
        return True

    # В личке админом считается ТОЛЬКО владелец бота (ADMIN_ID), проверенный выше.
    # Раньше здесь стоял безусловный `return True`, и любой желающий мог написать
    # боту в ЛС и выполнить /resetstats all или /addword — списки слов глобальные.
    if chat.type == "private":
        return False

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
                f"❌ Пользователь `{text_utils.escape_md(username)}` не найден в базе данных бота.\n"
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

    # Telegram трактует срок меньше 30 секунд как «навсегда», поэтому /mute 0
    # выдавал вечный мут вместо ошибки. Ограничиваем сверху годом.
    if minutes < 1 or minutes > 525600:
        await update.effective_message.reply_text(
            "⚠️ Длительность мута указывается в минутах, от `1` до `525600` (год).\n"
            "Пример: `/mute 15 @username Причина`",
            parse_mode="Markdown"
        )
        return

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
            f"📌 *Причина:* {text_utils.escape_md(reason)}",
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
                f"❌ Пользователь `{text_utils.escape_md(username)}` не найден в базе данных бота.\n"
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
                f"❌ Пользователь `{text_utils.escape_md(username)}` не найден в базе данных бота.",
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
        f"📌 *Причина:* {text_utils.escape_md(reason)}\n"
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
        await update.message.reply_text(f"✅ Слово `{text_utils.escape_md(word)}` успешно добавлено в стоп-лист.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Слово `{text_utils.escape_md(word)}` уже есть в стоп-листе.", parse_mode="Markdown")


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
        await update.message.reply_text(f"✅ Слово `{text_utils.escape_md(word)}` удалено из стоп-листа.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Слово `{text_utils.escape_md(word)}` не найдено в стоп-листе.", parse_mode="Markdown")


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


async def cmd_addsafeword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /addsafeword <word> (or /allowword)."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    if not context.args:
        await update.message.reply_text("⚠️ Укажите слово: `/addsafeword <слово>`", parse_mode="Markdown")
        return

    word = " ".join(context.args).strip().lower()

    # CRITICAL VALIDATION: Hard profanity (мат) CANNOT be added to allowed words under any circumstances!
    from conflict_detector import is_hard_profanity
    if is_hard_profanity(word):
        await update.message.reply_text(
            f"❌ **Слово `{text_utils.escape_md(word)}` является матом или его разновидностью!**\n"
            f"Матерные слова (включая `пиздец`, `пздц`, `п3дц`, `хуй`, `ебать` и т.д.) категорически запрещено добавлять в разрешённые.",
            parse_mode="Markdown"
        )
        return

    success = await database.add_allowed_word(DB_PATH, word)
    if success:
        await update.message.reply_text(f"✅ Слово `{text_utils.escape_md(word)}` успешно добавлено в список разрешённых слов.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Слово `{text_utils.escape_md(word)}` уже есть в списке разрешённых.", parse_mode="Markdown")


async def cmd_removesafeword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /removesafeword <word>."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    if not context.args:
        await update.message.reply_text("⚠️ Укажите слово: `/removesafeword <слово>`", parse_mode="Markdown")
        return

    word = " ".join(context.args).strip().lower()
    success = await database.remove_allowed_word(DB_PATH, word)
    if success:
        await update.message.reply_text(f"✅ Слово `{text_utils.escape_md(word)}` удалено из списка разрешённых слов.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ Слово `{text_utils.escape_md(word)}` не найдено в списке разрешённых.", parse_mode="Markdown")


async def cmd_safewordlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /safewordlist."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    words = await database.get_allowed_words(DB_PATH)
    if not words:
        await update.message.reply_text("📋 Список разрешённых слов пуст.")
        return

    words_formatted = "\n".join(f"• `{w}`" for w in words)
    await update.message.reply_text(f"📋 **Список разрешённых слов ({len(words)}):**\n\n{words_formatted}", parse_mode="Markdown")


async def cmd_resetstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /resetstats [@username | user_id | all] (or as reply)."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    # Check if admin requested to reset ALL users at once (/resetstats all or /resetstats все)
    if context.args and context.args[0].strip().lower() in ("all", "все", "*"):
        await database.reset_all_users_stats(DB_PATH)
        await update.effective_message.reply_text("🔄 **Статистика нарушений и варны для ВСЕХ пользователей чата успешно сброшены!**\n*(Защита капчи для несертифицированных ботов осталась нетронутой).* ", parse_mode="Markdown")
        return

    user_id, username, mention = await _resolve_target_user(update, context)
    if not user_id:
        if username and username.startswith("@"):
            await update.message.reply_text(
                f"❌ Пользователь `{text_utils.escape_md(username)}` не найден в базе данных бота.",
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
    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=user_id,
            permissions=await get_default_permissions(context, update.effective_chat.id)
        )
    except Exception:
        pass
    await update.effective_message.reply_text(f"✅ Статистика нарушений и мут для {mention} полностью сброшены.", parse_mode="Markdown")





async def cmd_uncaptcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command /uncaptcha [@username | ID | reply] to manually approve/remove captcha for a user."""
    if not await is_admin(update, context):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    await _track_admin(update, context)

    user_id, username, mention = await _resolve_target_user(update, context)
    if not user_id:
        if username and username.startswith("@"):
            await update.message.reply_text(
                f"❌ Пользователь `{text_utils.escape_md(username)}` ещё не зафиксирован в базе бота (ему нужно отправить хотя бы одно сообщение или перезайти).\n"
                f"Снимите капчу ответом на его сообщение в чате или укажите его Telegram ID.",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                "⚠️ Укажите пользователя ответом на сообщение или `@username` / `ID`:\n"
                "Пример: `/uncaptcha @username` или `/uncaptcha 12345678`",
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
