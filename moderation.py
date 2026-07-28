import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, Message, ChatPermissions
from telegram.ext import ContextTypes
from telegram.error import TelegramError, Forbidden, BadRequest

from config import (
    ADMIN_ID,
    DB_PATH,
    MUTE_STEP_2_MINUTES,
    MUTE_STEP_3_MINUTES,
    MUTE_STEP_4_MINUTES,
    MUTE_STEP_5_MINUTES
)
import database
import notification
import text_utils

logger = logging.getLogger("PeaceMirorBot.moderation")

# Permissions for Muted User (All sending disabled)
MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False
)

# Permissions for Unmuted Normal User
UNMUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True
)


async def get_default_permissions(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> ChatPermissions:
    """
    Права, которые надо вернуть при размуте. Берём реальные настройки чата, а не
    константу: UNMUTED_PERMISSIONS не включает закрепление сообщений и изменение
    профиля чата, и после мута пользователь тихо терял эти права.
    """
    try:
        chat = await context.bot.get_chat(chat_id)
        if chat.permissions:
            return chat.permissions
    except TelegramError as e:
        logger.warning(f"Could not read default permissions for chat {chat_id}: {e}")
    return UNMUTED_PERMISSIONS


def format_mute_duration(minutes: int) -> str:
    """Formats mute duration in minutes to a human-readable Russian string."""
    if minutes < 60:
        return f"{minutes} мин."
    elif minutes < 1440:
        hours = minutes // 60
        remaining_min = minutes % 60
        if remaining_min == 0:
            return f"{hours} ч."
        return f"{hours} ч. {remaining_min} мин."
    else:
        days = minutes // 1440
        remaining_hours = (minutes % 1440) // 60
        if remaining_hours == 0:
            if days == 1:
                return "1 день"
            elif days < 5:
                return f"{days} дня"
            else:
                return f"{days} дней"
        if days == 1:
            return f"1 день {remaining_hours} ч."
        elif days < 5:
            return f"{days} дня {remaining_hours} ч."
        else:
            return f"{days} дней {remaining_hours} ч."


def calculate_mute_minutes(violation_count: int) -> int:
    """
    Calculates mute duration in minutes based on violation count.
    Steps 1-4 use fixed values from config. Step 5+ uses exponential doubling.

    Ladder:
      1 → warning only (0 min)
      2 → MUTE_STEP_2_MINUTES (default 5 min)
      3 → MUTE_STEP_3_MINUTES (default 15 min)
      4 → MUTE_STEP_4_MINUTES (default 120 min / 2 hours)
      5 → MUTE_STEP_5_MINUTES (default 1440 min / 24 hours)
      6 → 2880 min (48 hours) — x2
      7 → 5760 min (96 hours / 4 days) — x2
      ... and so on
    """
    if violation_count <= 1:
        return 0
    elif violation_count == 2:
        return MUTE_STEP_2_MINUTES
    elif violation_count == 3:
        return MUTE_STEP_3_MINUTES
    elif violation_count == 4:
        return MUTE_STEP_4_MINUTES
    else:
        # Step 5+: exponential doubling from base MUTE_STEP_5_MINUTES
        exponent = violation_count - 5  # 5→0 (1440m), 6→1 (2880m), 7→2 (5760m)...
        return MUTE_STEP_5_MINUTES * (2 ** exponent)


async def notify_admin_missing_rights(context: ContextTypes.DEFAULT_TYPE, action: str, chat_id: int, chat_title: str):
    """Sends notification to active admins if the bot lacks required permissions."""
    text = (
        f"🚨 **Ошибка прав бота!**\n\n"
        f"Бот PeaceMirorBot не смог выполнить действие `{action}` в чате «{chat_title}».\n"
        f"Убедитесь, что бот является администратором чата с правами:\n"
        f"- Удаление сообщений (Delete messages)\n"
        f"- Блокировка / Ограничение участников (Restrict/Ban users)"
    )
    await notification.notify_admins(context, chat_id, text)


async def process_violation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    matched_word: str
):
    """
    Processes a conflict/profanity violation based on the mute ladder
    (значения берутся из config, ниже — дефолты):
    1: Peace warning in chat
    2: Delete message + Mute 5 min
    3: Delete message + Mute 15 min
    4: Delete message + Mute 2 hours
    5+: Delete message + Mute with exponential doubling (24h → 48h → 96h → ...)
    """
    message: Message = update.effective_message
    if not message or not message.from_user:
        return

    user = message.from_user
    chat = update.effective_chat
    user_id = user.id
    username = f"@{user.username}" if user.username else user.full_name
    chat_title = chat.title if chat else "Чат"

    # Record violation in DB
    new_count = await database.record_violation(DB_PATH, user_id, username)
    logger.info(f"User {username} (ID: {user_id}) violation #{new_count}. Triggered by: '{matched_word}'")

    user_mention = text_utils.user_mention(user.full_name, user_id)

    # --- LADDER STEP 1: PEACE WARNING ---
    if new_count == 1:
        text = (
            f"🕊️ {user_mention}, **пожалуйста, сохраняйте спокойствие и вежливость!**\n"
            f"Оскорбления и нецензурная лексика в чате запрещены. Любой конфликт можно разрешить конструктивно.\n\n"
            f"📌 *Это ваше 1-е предупреждение.* Повторные оскорбления приведут к временному муту (режиму чтения)."
        )
        try:
            await message.reply_text(text, parse_mode="Markdown")
        except TelegramError as e:
            logger.warning(f"Markdown warning failed for user {user_id}: {e}. Sending plain text.")
            await message.reply_text(text.replace("**", "").replace("*", ""))

    # --- LADDER STEP 2+: DELETE + MUTE (escalating) ---
    else:
        # Delete the offending message
        try:
            await message.delete()
        except (Forbidden, BadRequest) as e:
            logger.warning(f"Could not delete message for step {new_count}: {e}")
            await notify_admin_missing_rights(context, "удаление сообщения", chat.id, chat_title)

        # Calculate mute duration
        mute_minutes = calculate_mute_minutes(new_count)
        duration_str = format_mute_duration(mute_minutes)
        until_date = datetime.now(timezone.utc) + timedelta(minutes=mute_minutes)

        # Apply mute
        muted = False
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user_id,
                permissions=MUTED_PERMISSIONS,
                until_date=until_date
            )
            muted = True
            await database.set_user_mute(DB_PATH, chat_id=chat.id, user_id=user_id, username=username, muted_until=until_date)
        except (Forbidden, BadRequest) as e:
            logger.error(f"Could not restrict user {user_id}: {e}")
            await notify_admin_missing_rights(context, "ограничение участника (мут)", chat.id, chat_title)

        # Determine next step info for the message
        if new_count == 2:
            next_step_info = f"Следующее приведёт к муту на {format_mute_duration(MUTE_STEP_3_MINUTES)}."
        elif new_count == 3:
            next_step_info = f"Следующее приведёт к муту на {format_mute_duration(MUTE_STEP_4_MINUTES)}."
        else:
            next_mute = calculate_mute_minutes(new_count + 1)
            next_step_info = f"Следующее приведёт к муту на {format_mute_duration(next_mute)}."

        if muted:
            text = (
                f"🤐 {user_mention} переведён в **режим чтения на {duration_str}**.\n"
                f"Сообщение удалено за оскорбления/мат. Время остыть и продолжить беседу спокойно.\n\n"
                f"📌 *Это ваше {new_count}-е нарушение.* {next_step_info}"
            )
        else:
            text = (
                f"⚠️ Сообщение {user_mention} удалено за нарушение правил!\n"
                f"📌 *Это ваше {new_count}-е нарушение.* (По ограничениям Telegram бот не может применить режим чтения к администратору, но сообщение удалено)."
            )

        try:
            await context.bot.send_message(chat_id=chat.id, text=text, parse_mode="Markdown")
        except TelegramError as e:
            logger.warning(f"Markdown mute notice failed in chat {chat.id}: {e}. Sending plain text.")
            await context.bot.send_message(chat_id=chat.id, text=text.replace("**", "").replace("*", ""))

