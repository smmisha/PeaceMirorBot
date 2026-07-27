import logging
from telegram.ext import ContextTypes
from telegram.error import TelegramError, Forbidden, BadRequest

from config import DB_PATH, RESET_INACTIVE_DAYS
from moderation import UNMUTED_PERMISSIONS
import database

logger = logging.getLogger("PeaceMirorBot.background")


async def job_check_expired_mutes(context: ContextTypes.DEFAULT_TYPE):
    """Periodic job to restore writing permissions for users whose mutes have expired."""
    try:
        expired_mutes = await database.get_expired_mutes(DB_PATH)
        if not expired_mutes:
            return

        logger.info(f"Found {len(expired_mutes)} expired mutes to process.")

        for item in expired_mutes:
            chat_id = item["chat_id"]
            user_id = item["user_id"]
            username = item.get("username") or f"ID {user_id}"

            try:
                await context.bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    permissions=UNMUTED_PERMISSIONS
                )
                logger.info(f"Unmuted user {username} ({user_id}) in chat {chat_id}.")
            except (Forbidden, BadRequest) as e:
                logger.warning(f"Failed to restore permissions for user {user_id} in chat {chat_id}: {e}")
            except TelegramError as e:
                logger.error(f"Telegram error restoring permissions for user {user_id}: {e}")

            # Mark mute as inactive in DB
            await database.clear_user_mute(DB_PATH, chat_id, user_id)

    except Exception as e:
        logger.error(f"Error in job_check_expired_mutes: {e}", exc_info=True)


async def job_reset_inactive_violations(context: ContextTypes.DEFAULT_TYPE):
    """Periodic job to decay/reset violation history for users inactive for RESET_INACTIVE_DAYS."""
    try:
        reset_count = await database.reset_inactive_violations(DB_PATH, days=RESET_INACTIVE_DAYS)
        if reset_count > 0:
            logger.info(f"Reset violations for {reset_count} users inactive for >= {RESET_INACTIVE_DAYS} days.")
    except Exception as e:
        logger.error(f"Error in job_reset_inactive_violations: {e}", exc_info=True)
