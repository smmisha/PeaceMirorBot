import logging
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config import ADMIN_ID, DB_PATH
import database

logger = logging.getLogger("PeaceMirorBot.notification")

# Track which notifications are pending (keyed by a unique event identifier)
# Format: {event_key: {"admins_notified": [user_id, ...], "resolved": bool}}
_pending_notifications: dict = {}


async def notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    event_key: str | None = None
):
    """
    Smart admin notification routing:
    1. Gets the most recently active admin for this chat from DB
    2. Sends notification to them
    3. Schedules a 5-minute fallback: if no admin reacted, notify the next admin
    4. If all admins exhausted or none found, falls back to ADMIN_ID

    Args:
        context: Telegram bot context
        chat_id: The chat where the event occurred
        text: Notification message text
        event_key: Optional unique key for this event (for fallback tracking)
    """
    # Get list of admins sorted by most recently active
    active_admins = await database.get_active_admins(DB_PATH, chat_id)

    if not active_admins:
        # No known active admins — send directly to ADMIN_ID
        await _send_to_admin(context, ADMIN_ID, text)
        return

    # Try the most recently active admin first
    first_admin_id = active_admins[0]["user_id"]
    sent = await _send_to_admin(context, first_admin_id, text)

    if not sent:
        # If failed to reach most active admin, try fallback chain
        await _fallback_notify(context, active_admins[1:], text)
        return

    # Schedule fallback notification after 5 minutes
    if event_key and len(active_admins) > 1 and context.job_queue:
        remaining_admins = [a["user_id"] for a in active_admins[1:]]
        context.job_queue.run_once(
            _fallback_job,
            when=300,  # 5 minutes
            data={
                "text": text,
                "remaining_admin_ids": remaining_admins,
                "chat_id": chat_id,
                "event_key": event_key,
                "first_admin_id": first_admin_id
            },
            name=f"notify_fallback_{event_key}"
        )
        _pending_notifications[event_key] = {
            "admins_notified": [first_admin_id],
            "resolved": False
        }


def mark_notification_resolved(event_key: str):
    """Call this when an admin reacts (e.g. uses /mute, /warn) to cancel fallback."""
    if event_key in _pending_notifications:
        _pending_notifications[event_key]["resolved"] = True
        # Clean up old entries (keep last 100)
        if len(_pending_notifications) > 100:
            keys = list(_pending_notifications.keys())
            for old_key in keys[:-100]:
                del _pending_notifications[old_key]


async def _send_to_admin(context: ContextTypes.DEFAULT_TYPE, admin_id: int | None, text: str) -> bool:
    """Attempts to send a message to a specific admin. Returns True on success."""
    if not admin_id:
        return False
    try:
        await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
        return True
    except TelegramError as e:
        logger.warning(f"Failed to send notification to admin {admin_id}: {e}")
        return False


async def _fallback_notify(
    context: ContextTypes.DEFAULT_TYPE,
    remaining_admins: list[dict],
    text: str
):
    """Tries to notify the next admin in the list, falls back to ADMIN_ID if all fail."""
    for admin in remaining_admins:
        sent = await _send_to_admin(context, admin["user_id"], text)
        if sent:
            return

    # All active admins failed — fall back to global ADMIN_ID
    await _send_to_admin(context, ADMIN_ID, text)


async def _fallback_job(context: ContextTypes.DEFAULT_TYPE):
    """Job callback: if the notification was not resolved, notify the next admin."""
    data = context.job.data
    event_key = data["event_key"]

    # Check if already resolved (an admin reacted)
    pending = _pending_notifications.get(event_key)
    if pending and pending.get("resolved"):
        logger.info(f"Notification {event_key} already resolved, skipping fallback.")
        # Clean up
        _pending_notifications.pop(event_key, None)
        return

    text = data["text"]
    remaining_ids = data["remaining_admin_ids"]

    # Try next admin in the chain
    for admin_id in remaining_ids:
        if pending and admin_id in pending.get("admins_notified", []):
            continue

        fallback_text = f"⏰ *Повторное уведомление (предыдущий админ не отреагировал):*\n\n{text}"
        sent = await _send_to_admin(context, admin_id, fallback_text)
        if sent:
            if pending:
                pending["admins_notified"].append(admin_id)
            logger.info(f"Fallback notification sent to admin {admin_id} for event {event_key}")
            return

    # If all active admins exhausted, fall back to ADMIN_ID
    if ADMIN_ID and (not pending or ADMIN_ID not in pending.get("admins_notified", [])):
        fallback_text = f"⏰ *Уведомление (ни один активный админ не ответил):*\n\n{text}"
        await _send_to_admin(context, ADMIN_ID, fallback_text)

    # Clean up
    _pending_notifications.pop(event_key, None)
