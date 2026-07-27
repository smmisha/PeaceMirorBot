import os
import asyncio
import sys
import logging
from telegram import Update, BotCommand, BotCommandScopeDefault, BotCommandScopeAllChatAdministrators
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatMemberHandler,
    filters,
    ContextTypes
)

from config import BOT_TOKEN, ADMIN_ID, DB_PATH, logger
import database
from background import job_check_expired_mutes, job_reset_inactive_violations
from handlers import admin, user, messages, captcha


async def job_check_chat_inactivity(context):
    """Job to check if a chat has been silent for >= 7 hours (25200s), sending 👀 emoji once."""
    import time
    active_chats = context.bot_data.get("active_chats", set())
    now_ts = time.time()
    INACTIVITY_7H = 7 * 3600  # 7 hours in seconds

    for chat_id in list(active_chats):
        last_msg_time = context.bot_data.get(f"last_msg_time_{chat_id}", 0)
        eyes_sent = context.bot_data.get(f"eyes_sent_{chat_id}", False)

        if last_msg_time > 0 and (now_ts - last_msg_time >= INACTIVITY_7H) and not eyes_sent:
            try:
                await context.bot.send_message(chat_id=chat_id, text="👀")
                context.bot_data[f"eyes_sent_{chat_id}"] = True
                logger.info(f"Chat {chat_id} silent for 7+ hours, sent 👀 emoji.")
            except Exception as e:
                logger.warning(f"Failed to send 7h inactivity 👀 emoji to chat {chat_id}: {e}")


async def post_init(application):
    """Callback run after application initialization to prepare database, background jobs, and bot menu commands."""
    logger.info("Initializing database connection...")
    db_ok = await database.init_db(DB_PATH)
    if not db_ok:
        logger.critical("FATAL: Database connection failed during startup! Shutting down bot.")
        sys.exit(1)

    logger.info("Database initialized successfully.")

    try:
        bot_user = await application.bot.get_me()
        application.bot_data["bot_username"] = bot_user.username
        logger.info(f"Bot authenticated as @{bot_user.username} (ID: {bot_user.id})")
    except Exception as e:
        logger.warning(f"Could not fetch get_me info: {e}")

    # Schedule repeating 7-hour inactivity check job every 30 minutes
    if application.job_queue:
        application.job_queue.run_repeating(job_check_chat_inactivity, interval=1800, first=300, name="inactivity_check_job")

    # Register Bot Menu Commands in Telegram
    try:
        user_commands = [
            BotCommand("start", "Запустить бота / Справка"),
            BotCommand("rules", "Правила общения в чате"),
            BotCommand("mystats", "Моя статистика нарушений"),
            BotCommand("report", "Пожаловаться админам (ответом)"),
            BotCommand("ai", "Задать вопрос ИИ (/ai <вопрос>)"),
            BotCommand("meme", "Прислать смешной мем или картинку"),
            BotCommand("top", "Рейтинг Топ-10 Миротворцев чата"),
            BotCommand("summary", "Итоги общения / Выжимка чата"),
            BotCommand("help", "Справка по командам"),
        ]
        await application.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())

        admin_commands = [
            BotCommand("mute", "Выдать мут (ответом или /mute 15 @username)"),
            BotCommand("unmute", "Снять мут (ответом или /unmute @username)"),
            BotCommand("warn", "Выдать предупреждение (/warn @username)"),
            BotCommand("addword", "Добавить слово в стоп-лист (/addword <слово>)"),
            BotCommand("removeword", "Удалить слово из стоп-листа"),
            BotCommand("wordlist", "Показать стоп-лист слов"),
            BotCommand("resetstats", "Сбросить статистику (/resetstats @username)"),
            BotCommand("chattag", "Вкл/Выкл случайные вклинивания ИИ (/chattag on/off)"),
            BotCommand("uncaptcha", "Снять капчу с пользователя (/uncaptcha @username)"),
            BotCommand("rules", "Правила общения в чате"),
            BotCommand("mystats", "Моя статистика нарушений"),
            BotCommand("report", "Пожаловаться админам (ответом)"),
            BotCommand("help", "Справка по командам"),
        ]
        await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeAllChatAdministrators())
        logger.info("Telegram Bot Menu commands configured successfully.")
    except Exception as e:
        logger.warning(f"Failed to set bot menu commands: {e}")

    if application.job_queue:
        # Check for expired mutes every 60 seconds
        application.job_queue.run_repeating(
            job_check_expired_mutes,
            interval=60,
            first=10,
            name="job_check_expired_mutes"
        )
        # Reset inactive violation counters daily
        application.job_queue.run_repeating(
            job_reset_inactive_violations,
            interval=86400,
            first=30,
            name="job_reset_inactive_violations"
        )
        logger.info("Background job queue configured successfully.")
    else:
        logger.warning("JobQueue is not enabled! Make sure python-telegram-bot[job-queue] is installed.")


def main():
    """Main entry point for starting the PeaceMirorBot Telegram bot."""
    if not BOT_TOKEN:
        logger.critical("BOT_TOKEN is empty! Please set BOT_TOKEN in environment or .env file.")
        sys.exit(1)

    logger.info(f"Starting PeaceMirorBot... Configured Admin ID: {ADMIN_ID}")

    # Proxy: Only set if TELEGRAM_PROXY is explicitly specified (ignore default PA HTTP_PROXY for api.telegram.org)
    proxy_url = os.getenv("TELEGRAM_PROXY")
    builder = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .read_timeout(30)
        .write_timeout(30)
        .connect_timeout(30)
        .get_updates_read_timeout(30)
    )
    if proxy_url:
        logger.info(f"Configuring Telegram proxy: {proxy_url}")
        builder = builder.proxy(proxy_url).get_updates_proxy(proxy_url)

    app = builder.build()

    # User Commands
    app.add_handler(CommandHandler("start", user.cmd_start))
    app.add_handler(CommandHandler("help", user.cmd_help))
    app.add_handler(CommandHandler("rules", user.cmd_rules))
    app.add_handler(CommandHandler("mystats", user.cmd_mystats))
    app.add_handler(CommandHandler("report", user.cmd_report))
    app.add_handler(CommandHandler("ai", user.cmd_ai))
    app.add_handler(CommandHandler("bot", user.cmd_ai))
    app.add_handler(CommandHandler("meme", user.cmd_meme))
    app.add_handler(CommandHandler("top", user.cmd_top))
    app.add_handler(CommandHandler("peacetop", user.cmd_top))
    app.add_handler(CommandHandler("summary", user.cmd_summary))
    app.add_handler(CommandHandler("digest", user.cmd_summary))

    # Admin Commands
    app.add_handler(CommandHandler("mute", admin.cmd_mute))
    app.add_handler(CommandHandler("unmute", admin.cmd_unmute))
    app.add_handler(CommandHandler("warn", admin.cmd_warn))
    app.add_handler(CommandHandler("addword", admin.cmd_addword))
    app.add_handler(CommandHandler("removeword", admin.cmd_removeword))
    app.add_handler(CommandHandler("resetstats", admin.cmd_resetstats))
    app.add_handler(CommandHandler("chattag", admin.cmd_chattag))
    app.add_handler(CommandHandler("uncaptcha", admin.cmd_uncaptcha))
    app.add_handler(CommandHandler("passcaptcha", admin.cmd_uncaptcha))

    # Captcha Handlers for New Chat Members (both member updates and service messages)
    app.add_handler(ChatMemberHandler(captcha.handle_chat_member_updated, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, captcha.handle_new_chat_members))
    app.add_handler(CallbackQueryHandler(captcha.handle_captcha_callback, pattern=r"^captcha_pass_"))

    # Group Messages Handler (text messages, edited messages, and voice messages)
    group_filter = ((filters.TEXT | filters.VOICE) & ~filters.COMMAND)
    app.add_handler(MessageHandler(group_filter, messages.handle_group_message))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE & group_filter, messages.handle_group_message))

    logger.info("Bot handlers registered. Starting long polling...")
    while True:
        try:
            app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        except Exception as e:
            logger.error(f"Bot polling error: {e}. Restarting in 5s...")
            import time
            time.sleep(5)


if __name__ == "__main__":
    main()
