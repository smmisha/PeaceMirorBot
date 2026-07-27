import logging
from telegram import Update, ChatMember
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import DB_PATH, ADMIN_ID
import database
from conflict_detector import find_violation
import moderation

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
    Evaluates incoming group chat messages and edited messages for profanity, insults, or conflict triggers.
    Chat admins are exempt from automatic moderation.
    """
    message = update.effective_message
    if not message or not message.text:
        return

    # Skip messages sent by bots
    if message.from_user and message.from_user.is_bot:
        return

    user = message.from_user
    chat = update.effective_chat

    # Skip moderation for admins (they are exempt from automatic punishment)
    if user and chat:
        if ADMIN_ID and user.id == ADMIN_ID:
            return
        if chat.type in ("group", "supergroup"):
            if await _is_chat_admin(context, chat.id, user.id):
                return

    # Retrieve custom bad words from database
    custom_bad_words = await database.get_bad_words(DB_PATH)

    has_violation, matched_word = find_violation(message.text, custom_bad_words)

    if has_violation and matched_word:
        logger.info(f"Conflict/profanity detected in message from user {message.from_user.id}: matched '{matched_word}'")
        await moderation.process_violation(update, context, matched_word)
