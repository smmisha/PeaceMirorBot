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

    # Retrieve custom bad words from database
    custom_bad_words = await database.get_bad_words(DB_PATH)

    has_violation, matched_word = find_violation(text_to_check, custom_bad_words)

    if has_violation and matched_word:
        logger.info(f"Conflict/profanity detected in message/voice from user {message.from_user.id}: matched '{matched_word}'")
        await moderation.process_violation(update, context, matched_word)
