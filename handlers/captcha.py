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


async def _is_chat_admin(context, chat_id: int, user_id: int) -> bool:
    """Checks if a user is an admin or owner in the chat."""
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except TelegramError:
        return False


def _safe_mention(full_name: str, user_id: int) -> str:
    """Safely formats user mention string escaping Markdown special characters."""
    escaped_name = (
        full_name.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("`", "\\`")
    )
    return f"[{escaped_name}](tg://user?id={user_id})"


async def _process_user_captcha(context: ContextTypes.DEFAULT_TYPE, chat, user):
    """Internal helper to mute a user, send captcha message, and schedule 3m msg delete + 24h kick jobs."""
    if user.is_bot:
        return

    user_id = user.id
    username_str = f"@{user.username}" if user.username else user.full_name
    await database.ensure_user_exists(DB_PATH, user_id, username_str)

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

                user_mention = _safe_mention(user.full_name, user_id)
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

    # Clear any old stale captcha jobs if user re-joined
    job_3m_name = f"captcha_timeout_3m_{chat.id}_{user_id}"
    job_24h_name = f"captcha_kick_24h_{chat.id}_{user_id}"

    if context.job_queue:
        for j in context.job_queue.get_jobs_by_name(job_3m_name) + context.job_queue.get_jobs_by_name(job_24h_name):
            j.schedule_removal()

    user_mention = _safe_mention(user.full_name, user_id)

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
        try:
            captcha_msg = await context.bot.send_message(
                chat_id=chat.id,
                text=caption,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Markdown send failed for captcha user {user_id}: {e}. Retrying plain text fallback.")
            plain_caption = (
                f"👋 Welcome, {user.full_name}!\n\n"
                f"Для защиты чата от автоматических ботов, пожалуйста, подтвердите, что вы человек.\n"
                f"Нажмите на кнопку ниже в течение 3 минут."
            )
            captcha_msg = await context.bot.send_message(
                chat_id=chat.id,
                text=plain_caption,
                reply_markup=keyboard
            )

        # Save active captcha message into persistent SQLite DB
        await database.save_active_captcha(DB_PATH, chat.id, user_id, captcha_msg.message_id)

        # 3. Schedule Stage 1 (3m delete public message)
        if context.job_queue:
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
    Triggers whenever the user joins or re-joins the chat (including from RESTRICTED state).
    """
    result = update.chat_member
    if not result:
        return

    user = result.new_chat_member.user
    if user.is_bot:
        return

    # Ignore actions performed by the bot itself (e.g. unmuting or restricting)
    if result.from_user and result.from_user.id == context.bot.id:
        return

    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status

    is_user_self_action = (result.from_user and result.from_user.id == user.id)
    is_join_transition = old_status in (ChatMember.LEFT, ChatMember.BANNED, ChatMember.RESTRICTED) and new_status in (ChatMember.MEMBER, ChatMember.RESTRICTED)

    if is_user_self_action or is_join_transition:
        logger.info(f"User {user.full_name} ({user.id}) joined/re-joined chat {result.chat.id} (from {old_status} to {new_status}). Triggering captcha.")
        await _process_user_captcha(context, result.chat, user)


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

    await database.remove_active_captcha(DB_PATH, chat_id, user_id)

    if context.job_queue:
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
        text_out = f"🟢 Администратор {_safe_mention(clicker_user.full_name, clicker_user.id)} вручную снял капчу у пользователя."
    else:
        await query.answer("✅ Капча успешно пройдена! Добро пожаловать!", show_alert=False)
        user_mention = _safe_mention(clicker_user.full_name, clicker_user.id)
        text_out = f"🟢 {user_mention} успешно прошел/прошла проверку капчи. Приятного общения!"

    # Delete the captcha message
    if query.message:
        try:
            await query.message.delete()
        except Exception as e:
            logger.warning(f"Could not delete captcha message on button click: {e}")

    # Send temporary welcome text (auto-deletes after 10 seconds)
    try:
        welcome = await context.bot.send_message(
            chat_id=chat.id,
            text=text_out,
            parse_mode="Markdown"
        )
        if context.job_queue:
            context.job_queue.run_once(
                job_delete_message,
                when=10,
                data={"chat_id": chat.id, "msg_id": welcome.message_id},
                name=f"del_welcome_{chat.id}_{welcome.message_id}"
            )
    except Exception as e:
        logger.warning(f"Failed sending captcha welcome message: {e}")


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
    except Exception as e:
        logger.warning(f"Could not delete 3m captcha message {captcha_msg_id} in chat {chat_id}: {e}")

    await database.remove_active_captcha(DB_PATH, chat_id, user_id)
    logger.info(f"Stage 1: 3m captcha message deleted for user {user_id} in chat {chat_id}. User remains muted (24h kick timer active).")


async def job_cleanup_expired_captchas(context: ContextTypes.DEFAULT_TYPE):
    """
    Background job running every 60s to clean up captcha messages older than 3 minutes (180s)
    from SQLite, guaranteeing deletion even after bot restarts.
    """
    try:
        expired = await database.get_expired_captchas(DB_PATH, CAPTCHA_TIMEOUT_3M)
        for item in expired:
            c_id = item["chat_id"]
            u_id = item["user_id"]
            m_id = item["message_id"]
            try:
                await context.bot.delete_message(chat_id=c_id, message_id=m_id)
                logger.info(f"Background cleanup deleted expired captcha message {m_id} for user {u_id} in chat {c_id}.")
            except Exception as e:
                logger.warning(f"Background cleanup: failed to delete captcha message {m_id} in chat {c_id}: {e}")
            await database.remove_active_captcha(DB_PATH, c_id, u_id)
    except Exception as e:
        logger.error(f"Error in job_cleanup_expired_captchas: {e}")


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
