import logging
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import ContextTypes
from telegram.error import TelegramError, Forbidden, BadRequest

from config import DB_PATH
from moderation import MUTED_PERMISSIONS, UNMUTED_PERMISSIONS
import database

logger = logging.getLogger("PeaceMirorBot.handlers.captcha")

CAPTCHA_TIMEOUT_3M = 180   # 3 minutes for chat message auto-delete
CAPTCHA_KICK_24H = 86400    # 24 hours before kicking unverified user


async def _process_user_captcha(context: ContextTypes.DEFAULT_TYPE, chat, user):
    """Internal helper to mute a user, send captcha message, and schedule 3m msg delete + 24h kick jobs."""
    if user.is_bot:
        return

    user_id = user.id

    # 0. CHECK IF USER HAS AN ACTIVE PUNISHMENT MUTE IN DATABASE!
    active_mute = await database.get_active_user_mute(DB_PATH, chat.id, user_id)
    if active_mute and active_mute.get("muted_until"):
        try:
            muted_until_str = active_mute["muted_until"]
            muted_until_dt = datetime.fromisoformat(muted_until_str)
            now_dt = datetime.now(timezone.utc)

            if muted_until_dt > now_dt:
                # Re-apply mute in Telegram with remaining duration!
                await context.bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user_id,
                    permissions=MUTED_PERMISSIONS,
                    until_date=muted_until_dt
                )
                from moderation import format_mute_duration
                remaining_minutes = max(1, int((muted_until_dt - now_dt).total_seconds() // 60))
                duration_str = format_mute_duration(remaining_minutes)

                user_mention = f"[{user.full_name}](tg://user?id={user_id})"
                await context.bot.send_message(
                    chat_id=chat.id,
                    text=(
                        f"🤐 {user_mention} перезашел/перезашла в группу, но действует **активный мут за нарушения**!\n"
                        f"Ограничения возобновлены. Осталось остывать: **{duration_str}**."
                    ),
                    parse_mode="Markdown"
                )
                logger.info(f"Re-applied active mute for user {user_id} re-joining chat {chat.id}.")
                return  # DO NOT SHOW CAPTCHA & DO NOT UNMUTE!
        except Exception as e:
            logger.error(f"Error checking/re-applying active mute for re-joining user {user_id}: {e}")

    # Deduplication check: ignore if user already has an active captcha in progress
    job_3m_name = f"captcha_timeout_3m_{chat.id}_{user_id}"
    job_24h_name = f"captcha_kick_24h_{chat.id}_{user_id}"

    existing_3m = context.job_queue.get_jobs_by_name(job_3m_name)
    existing_24h = context.job_queue.get_jobs_by_name(job_24h_name)

    if existing_3m or existing_24h:
        logger.info(f"Captcha already active for user {user.full_name} ({user_id}) in chat {chat.id}, skipping duplicate.")
        return

    user_mention = f"[{user.full_name}](tg://user?id={user_id})"

    # 1. Immediately restrict user permissions (mute)
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=MUTED_PERMISSIONS
        )
    except (Forbidden, BadRequest) as e:
        logger.warning(f"Failed to restrict member {user_id} in chat {chat.id}: {e}")

    # 2. Build Captcha Inline Keyboard
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Я человек / Подтвердить", callback_data=f"captcha_pass_{user_id}")]
    ])

    caption = (
        f"👋 Welcome, {user_mention}!\n\n"
        f"Для защиты чата от автоматических ботов, пожалуйста, подтвердите, что вы человек.\n"
        f"Нажмите на кнопку ниже в течение **3 минут**."
    )

    try:
        captcha_msg = await context.bot.send_message(
            chat_id=chat.id,
            text=caption,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

        # 3. Schedule Stage 1 (3m delete public message)
        context.job_queue.run_once(
            job_captcha_timeout_3m,
            when=CAPTCHA_TIMEOUT_3M,
            data={
                "chat_id": chat.id,
                "user_id": user_id,
                "user_name": user.full_name,
                "captcha_msg_id": captcha_msg.message_id
            },
            name=job_3m_name
        )

        # 4. Schedule Stage 2 (24h kick if unverified)
        context.job_queue.run_once(
            job_captcha_kick_24h,
            when=CAPTCHA_KICK_24H,
            data={
                "chat_id": chat.id,
                "user_id": user_id,
                "user_name": user.full_name
            },
            name=job_24h_name
        )
    except Exception as e:
        logger.error(f"Error sending captcha message for user {user_id}: {e}")


async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler triggered when new members join the chat via service messages.
    """
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return

    new_members = message.new_chat_members
    if not new_members:
        return

    for user in new_members:
        await _process_user_captcha(context, chat, user)


async def handle_chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler triggered when a user joins or re-joins via invite link / join button.
    Only triggers on transitions from LEFT or BANNED (never RESTRICTED, to avoid loops on unmuting).
    """
    result = update.chat_member
    if not result:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    user = result.new_chat_member.user

    if user.is_bot:
        return

    # Trigger ONLY when user joins from LEFT or BANNED state
    if old_status in (ChatMember.LEFT, ChatMember.BANNED) and new_status in (ChatMember.MEMBER, ChatMember.RESTRICTED):
        logger.info(f"User {user.full_name} ({user.id}) joined/re-joined chat {result.chat.id} (from {old_status} to {new_status}). Triggering captcha.")
        await _process_user_captcha(context, result.chat, user)


async def handle_captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles button presses for captcha verification.
    """
    query = update.callback_query
    if not query or not query.data:
        return

async def approve_user_captcha(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int) -> bool:
    """Unmutes user, removes pending captcha jobs, and restores writing permissions."""
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user_id,
            permissions=UNMUTED_PERMISSIONS
        )
    except Exception as e:
        logger.error(f"Error restoring permissions for user {user_id}: {e}")

    job_3m_name = f"captcha_timeout_3m_{chat_id}_{user_id}"
    job_24h_name = f"captcha_kick_24h_{chat_id}_{user_id}"
    for job in context.job_queue.get_jobs_by_name(job_3m_name) + context.job_queue.get_jobs_by_name(job_24h_name):
        job.schedule_removal()

    return True


async def handle_captcha_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback query handler for when a user or admin clicks the captcha pass button."""
    query = update.callback_query
    if not query:
        return

    data = query.data
    if not data.startswith("captcha_pass_"):
        return

    target_user_id = int(data.replace("captcha_pass_", ""))
    clicker_user = query.from_user
    chat = update.effective_chat

    is_admin_click = await _is_chat_admin(context, chat.id, clicker_user.id)

    # Check if the person clicking is the target user OR an admin
    if clicker_user.id != target_user_id and not is_admin_click:
        await query.answer("⚠️ Эта капча предназначена не для вас!", show_alert=True)
        return

    # Approve captcha and restore writing permissions
    await approve_user_captcha(context, chat.id, target_user_id)

    if is_admin_click and clicker_user.id != target_user_id:
        await query.answer("✅ Капча у пользователя успешно снята администратором!", show_alert=False)
        text_out = f"🟢 Администратор [{clicker_user.full_name}](tg://user?id={clicker_user.id}) вручную снял капчу у пользователя."
    else:
        await query.answer("✅ Капча успешно пройдена! Добро пожаловать!", show_alert=False)
        user_mention = f"[{clicker_user.full_name}](tg://user?id={clicker_user.id})"
        text_out = f"🟢 {user_mention} успешно прошел/прошла проверку капчи. Приятного общения!"

    # Delete the captcha message
    if query.message:
        try:
            await query.message.delete()
        except Exception:
            pass

    # Send temporary welcome text (auto-deletes after 10 seconds)
    welcome = await context.bot.send_message(
        chat_id=chat.id,
        text=text_out,
        parse_mode="Markdown"
    )

    # Schedule deletion of welcome message
    context.job_queue.run_once(
        job_delete_message,
        when=10,
        data={"chat_id": chat.id, "msg_id": welcome.message_id},
        name=f"del_welcome_{chat.id}_{welcome.message_id}"
    )


async def job_captcha_timeout_3m(context: ContextTypes.DEFAULT_TYPE):
    """
    Stage 1: Deletes 3-minute public captcha message from chat.
    User remains muted waiting for 24h kick timeout.
    """
    data = context.job.data
    chat_id = data["chat_id"]
    user_id = data["user_id"]
    captcha_msg_id = data["captcha_msg_id"]

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=captcha_msg_id)
    except Exception:
        pass

    logger.info(f"Stage 1: 3m captcha message deleted for user {user_id} in chat {chat_id}. User remains muted (24h kick timer active).")


async def job_captcha_kick_24h(context: ContextTypes.DEFAULT_TYPE):
    """
    Stage 2: Kicks unverified user after 24 hours of failing to pass captcha.
    """
    data = context.job.data
    chat_id = data["chat_id"]
    user_id = data["user_id"]
    user_name = data.get("user_name", f"ID: {user_id}")

    try:
        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
        logger.info(f"Stage 2: Kicked unverified user {user_name} ({user_id}) from chat {chat_id} after 24h captcha timeout.")
    except Exception as e:
        logger.warning(f"Failed 24h kick for user {user_id}: {e}")


async def job_delete_message(context: ContextTypes.DEFAULT_TYPE):
    """Job callback to delete temporary messages."""
    data = context.job.data
    try:
        await context.bot.delete_message(chat_id=data["chat_id"], message_id=data["msg_id"])
    except Exception:
        pass


async def trigger_captcha_for_user(context: ContextTypes.DEFAULT_TYPE, chat, user_id: int, user_name: str | None = None):
    """
    Triggers captcha verification for a user (e.g. after admin /unmute).
    Mutes user, sends captcha message, and schedules 3m delete + 24h kick jobs.
    """
    # 1. Mute user
    try:
        await context.bot.restrict_chat_member(
            chat_id=chat.id,
            user_id=user_id,
            permissions=MUTED_PERMISSIONS
        )
    except (Forbidden, BadRequest) as e:
        logger.warning(f"Failed to restrict member {user_id} during manual captcha trigger: {e}")

    # 2. Build Captcha Inline Keyboard
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 Я человек / Подтвердить", callback_data=f"captcha_pass_{user_id}")]
    ])

    name_display = user_name or f"ID: {user_id}"
    user_mention = f"[{name_display}](tg://user?id={user_id})"

    caption = (
        f"👋 {user_mention}, администратор снял с вас мут!\n\n"
        f"Для защиты чата от ботов, пожалуйста, подтвердите, что вы человек.\n"
        f"Нажмите на кнопку ниже в течение **3 минут**."
    )

    job_3m_name = f"captcha_timeout_3m_{chat.id}_{user_id}"
    job_24h_name = f"captcha_kick_24h_{chat.id}_{user_id}"

    for j in context.job_queue.get_jobs_by_name(job_3m_name) + context.job_queue.get_jobs_by_name(job_24h_name):
        j.schedule_removal()

    try:
        captcha_msg = await context.bot.send_message(
            chat_id=chat.id,
            text=caption,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )

        context.job_queue.run_once(
            job_captcha_timeout_3m,
            when=CAPTCHA_TIMEOUT_3M,
            data={
                "chat_id": chat.id,
                "user_id": user_id,
                "user_name": name_display,
                "captcha_msg_id": captcha_msg.message_id
            },
            name=job_3m_name
        )

        context.job_queue.run_once(
            job_captcha_kick_24h,
            when=CAPTCHA_KICK_24H,
            data={
                "chat_id": chat.id,
                "user_id": user_id,
                "user_name": name_display
            },
            name=job_24h_name
        )
    except Exception as e:
        logger.error(f"Error sending captcha message for user {user_id}: {e}")
