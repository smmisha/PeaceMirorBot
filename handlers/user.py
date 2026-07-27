import logging
from telegram import Update
from telegram.ext import ContextTypes

from config import DB_PATH, ADMIN_ID
import database
import notification

logger = logging.getLogger("PeaceMirorBot.handlers.user")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command."""
    text = (
        "🕊️ **Добро пожаловать в PeaceMirorBot (Миротворец)!**\n\n"
        "Я создан для того, чтобы поддерживать дружелюбную и конструктивную атмосферу в вашем чате, "
        "предотвращать ссоры, оскорбления и использование нецензурной лексики.\n\n"
        "**Мои основные функции:**\n"
        "• Мониторинг оскорблений и мата в чате.\n"
        "• Система предупреждений и мутов (режима чтения) для остывания участника.\n"
        "• Команда `/report` в ответ на сообщение для вызова модераторов при ссоре.\n"
        "• Команда `/rules` для ознакомления с правилами общения.\n"
        "• Команда `/mystats` для просмотра вашей статистики.\n\n"
        "Добавьте меня в вашу группу и назначьте администратором с правами на удаление сообщений и ограничение участников!"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command."""
    text = (
        "📖 **Справка по командам PeaceMirorBot**\n\n"
        "**Для участников:**\n"
        "• `/mystats` — посмотреть свою статистику предупреждений\n"
        "• `/rules` — правила мирного общения в чате\n"
        "• `/report` — (в ответ на сообщение) пожаловаться админам на ссору или оскорбление\n\n"
        "**Для администраторов:**\n"
        "• `/mute <минуты> <причина>` — дать мут участнику (ответом на сообщение)\n"
        "• `/unmute` — снять мут с участника (ответом на сообщение)\n"
        "• `/warn <причина>` — выдача предупреждения (ответом на сообщение)\n"
        "• `/addword <слово>` — добавить слово в стоп-лист\n"
        "• `/removeword <слово>` — удалить слово из стоп-листа\n"
        "• `/wordlist` — показать список запрещённых слов\n"
        "• `/resetstats` — сбросить статистику нарушений пользователя"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /rules command."""
    text = (
        "📜 **Правила мирного общения в чате:**\n\n"
        "1️⃣ **Взаимное уважение**: Любые личные оскорбления, мат, унижение участников и переход на личности строго запрещены.\n"
        "2️⃣ **Урегулирование конфликтов**: Разногласия решаются аргументированно и спокойным тоном. В случае разгорающейся ссоры сделайте паузу.\n"
        "3️⃣ **Защита от токсичности**: Использование нецензурной лексики с целью задеть собеседника приводит к автоматическому муту.\n"
        "4️⃣ **Система эскалации**:\n"
        "   - 1-е нарушение: Миротворческое предупреждение\n"
        "   - 2-е нарушение: Мут на 5 минут\n"
        "   - 3-е нарушение: Мут на 15 минут\n"
        "   - 4-е нарушение: Мут на 2 часа\n"
        "   - 5-е нарушение: Мут на 24 часа\n"
        "   - 6-е+: Мут удваивается каждый раз (48ч → 96ч → 192ч → ...)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /mystats command."""
    user = update.effective_user
    if not user:
        return

    stats = await database.get_user_stats(DB_PATH, user.id)
    violations = stats.get("violations", 0)
    is_muted = stats.get("is_muted", 0)
    pts = stats.get("peace_points", 0)
    badge, title = database.get_rank_title(pts)

    status_str = "🟢 Нет активных мутов"
    if is_muted:
        muted_until = stats.get("muted_until", "Неизвестно")
        status_str = f"🔴 Активный мут (до {muted_until})"

    text = (
        f"📊 **Статистика участника [{user.full_name}](tg://user?id={user.id}):**\n\n"
        f"• Звание: {badge} **{title}**\n"
        f"• Репутация (баллы Миротворца): `{pts}` 🕊️\n"
        f"• Количество нарушений: `{violations}`\n"
        f"• Текущий статус: {status_str}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /report command (must be reply to a message)."""
    message = update.effective_message
    if not message.reply_to_message:
        await message.reply_text("⚠️ Команду `/report` нужно использовать в ответ на сообщение, о котором вы хотите сообщить.", parse_mode="Markdown")
        return

    target_msg = message.reply_to_message
    reporter = message.from_user
    chat = update.effective_chat

    report_text = (
        f"🚨 **Сигнал о конфликте / нарушении!**\n\n"
        f"• Отправитель отчёта: [{reporter.full_name}](tg://user?id={reporter.id})\n"
        f"• Чат: {chat.title}\n"
        f"• Автор сообщения: [{target_msg.from_user.full_name}](tg://user?id={target_msg.from_user.id})\n"
        f"• Текст сообщения: «{target_msg.text or '[Медиа/НЕТ ТЕКСТА]'}»"
    )

    # Use smart notification routing to reach available admin
    event_key = f"report_{chat.id}_{message.message_id}"
    await notification.notify_admins(context, chat.id, report_text, event_key=event_key)

    await message.reply_text("✅ Ваша жалоба отправлена модераторам. Спасибо за помощь в поддержании мира в чате!")


import groq_service

async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /ai command to directly ask AI a question."""
    message = update.effective_message
    if not message:
        return

    prompt = " ".join(context.args).strip() if context.args else ""
    if not prompt and message.reply_to_message:
        prompt = message.reply_to_message.text or ""

    if not prompt:
        await message.reply_text("💡 **Напишите вопрос к ИИ:**\nПример: `/ai что думаешь по поводу этого спора?`", parse_mode="Markdown")
        return

    user_name = message.from_user.full_name if message.from_user else "Участник"
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        ai_reply = await groq_service.generate_ai_reply(prompt, user_name)
        await message.reply_text(ai_reply, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in cmd_ai: {e}")


async def cmd_meme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /meme command to send a random meme or funny picture."""
    message = update.effective_message
    if not message:
        return

    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")
        url, caption = await groq_service.fetch_meme()
        if url:
            await message.reply_photo(photo=url, caption=caption)
        else:
            await message.reply_text("😅 Не удалось загрузить мем. Попробуйте еще раз!", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error sending meme: {e}")
        await message.reply_text("😅 Упс, не удалось отправить мем.")


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /top and /peacetop command to show top peacekeepers of the chat."""
    message = update.effective_message
    if not message:
        return

    top_users = await database.get_top_peacekeepers(DB_PATH, limit=10)
    if not top_users:
        await message.reply_text(
            "🕊️ **Рейтинг Миротворцев пока пуст!**\nОбщайтесь вежливо и поддерживайте друг друга, чтобы заработать первые баллы кармы!",
            parse_mode="Markdown"
        )
        return

    lines = ["🏆 **ТОП-10 МИРОТВОРЦЕВ ЧАТА:**\n"]
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    for idx, u in enumerate(top_users):
        pts = u.get("peace_points", 0)
        badge, title = database.get_rank_title(pts)
        uname = u.get("username")
        disp_name = f"@{uname}" if uname else f"ID: {u.get('user_id')}"
        medal = medals[idx] if idx < len(medals) else f"{idx+1}."
        lines.append(f"{medal} {disp_name} — **{pts}** баллов репутации ({badge} {title})")

    lines.append("\n✨ *Баллы репутации начисляются за культурное общение, поддержку и благодарности в чате!*")
    await message.reply_text("\n".join(lines), parse_mode="Markdown")
