import os
import io
import logging
import json
from typing import Optional, Tuple
from groq import Groq
from config import GROQ_API_KEY

logger = logging.getLogger("PeaceMirorBot.groq_service")

_client: Optional[Groq] = None

def _get_groq_client() -> Optional[Groq]:
    global _client
    if not GROQ_API_KEY:
        return None
    if _client is None:
        try:
            _client = Groq(api_key=GROQ_API_KEY)
        except Exception as e:
            logger.error(f"Failed to initialize Groq client: {e}")
            return None
    return _client


async def fetch_meme() -> Tuple[Optional[str], str]:
    """Fetches a 100% Russian meme photo URL and title (or cat photo as fallback)."""
    import httpx, random, re
    subreddits = ["Pikabu", "ru_memes"]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for _ in range(5):
                sub = random.choice(subreddits)
                resp = await client.get(f"https://meme-api.com/gimme/{sub}")
                if resp.status_code == 200:
                    data = resp.json()
                    url = data.get("url")
                    title = data.get("title", "🤪 Свежий мем")
                    # Enforce Russian Cyrillic title check to guarantee 100% Russian memes!
                    if url and not data.get("nsfw", False) and re.search(r'[а-яёА-ЯЁ]', title):
                        return url, f"🤣 {title}"
    except Exception as e:
        logger.warning(f"Error fetching Russian meme: {e}")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://api.thecatapi.com/v1/images/search")
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list):
                    return data[0].get("url"), "🐱 Милый котик для настроения!"
    except Exception:
        pass

    return None, ""


async def transcribe_voice(file_bytes: bytes, filename: str = "voice.ogg") -> Optional[str]:
    """
    Transcribes audio/voice bytes to Russian text using Groq Whisper (whisper-large-v3).
    Returns transcribed text or None on failure.
    """
    client = _get_groq_client()
    if not client:
        logger.warning("Groq API key not configured, skipping voice transcription.")
        return None

    try:
        audio_file = (filename, file_bytes, "audio/ogg")
        transcription = client.audio.transcriptions.create(
            model="whisper-large-v3",
            file=audio_file,
            language="ru",
            response_format="text"
        )
        text = str(transcription).strip()
        logger.info(f"Groq Whisper voice transcription result: '{text}'")
        return text
    except Exception as e:
        logger.error(f"Error during Groq Whisper voice transcription: {e}")
        return None


async def analyze_cultural_conflict(recent_messages: list[dict]) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Analyzes recent message history for escalation of polite/cultural conflict, bullying, or hurt feelings.
    Returns (is_conflict_escalating, peace_message, admin_alert).
    """
    client = _get_groq_client()
    if not client or not recent_messages:
        return False, None, None

    # Format recent conversation text for LLM
    chat_log = "\n".join(f"{m.get('sender', 'User')}: {m.get('text', '')}" for m in recent_messages[-8:])

    prompt = (
        "Ты — нейросетевой модератор-миротворец Telegram чата. Твоя задача — оценить, разгорается ли в чате сильный эмоциональный конфликт, "
        "ссора, обиды или завуалированная травля БЕЗ мата.\n\n"
        "Правила:\n"
        "1. Простой спор или обсуждение — это НЕ конфликт.\n"
        "2. Конфликтом считается ситуации, когда участники начинают открыто обижаться, задевать друг друга, уходить из чата или проявлять токсичность.\n"
        "3. Ответь СТРОГО в формате JSON:\n"
        '{"is_conflict": true/false, "peace_message": "Мягкое миротворческое сообщение в чат", "admin_alert": "Причина для админов"}\n\n'
        f"Диалог:\n{chat_log}"
    )

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3
        )
        content = response.choices[0].message.content
        data = json.loads(content)

        is_conflict = data.get("is_conflict", False)
        peace_msg = data.get("peace_message")
        admin_alert = data.get("admin_alert")

        return is_conflict, peace_msg, admin_alert
    except Exception as e:
        logger.error(f"Error analyzing conflict sentiment via Groq Llama: {e}")
        return False, None, None


def search_web_info(query: str, max_results: int = 3) -> Optional[str]:
    """
    Performs a live web search via DuckDuckGo (ddgs) and returns formatted search snippets.
    """
    try:
        from ddgs import DDGS
        results = list(DDGS().text(query, max_results=max_results))
        if not results:
            return None
        snippets = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            snippets.append(f"• {title}: {body}")
        return "\n".join(snippets)
    except Exception as e:
        logger.warning(f"Web search error: {e}")
        return None


async def generate_ai_reply(user_prompt: str, user_name: str = "Участник") -> str:
    """
    Generates a friendly, smart AI response using Groq Llama 3.3 70B with live web search capability.
    """
    client = _get_groq_client()
    if not client:
        return "🤖 AI модуль временно недоступен (не настроен GROQ_API_KEY)."

    # Determine if query requires live web search
    search_keywords = ["найди", "погугли", "новости", "погода", "курс", "сегодня", "интернет", "инет", "события", "кто такой", "что такое"]
    needs_search = any(kw in user_prompt.lower() for kw in search_keywords)

    web_results = None
    if needs_search:
        logger.info(f"Triggering live web search for query: '{user_prompt}'")
        web_results = search_web_info(user_prompt)

    system_prompt = (
        "Ты — Антиконфликт, дружелюбный, эмпатичный и отзывчивый ИИ-миротворец и поддерживающий собеседник в Telegram чате.\n\n"
        "Твои основные правила:\n"
        "1. ⚡ КРИТИЧЕСКАЯ КРАТКОСТЬ (СТРОГО!): Пиши ОЧЕНЬ коротко — МАКСИМУМ 1–2 небольших предложения! Никаких длинных текстов, абзацев и пассажей.\n"
        "2. 🤍 ЭМПАТИЯ И КОНТЕКСТ: Если участник делится СОБСТВЕННОЙ тревогой/усталостью/паникой — поддержать коротко и тепло. Если же участник просто даёт совет другому — ответь коротко как собеседник.\n"
        "3. 🌸 ГЕНДЕРНОЕ ОБРАЩЕНИЕ (СТРОГО!):\n"
        "   - Если имя пользователя явно женское (Маргарита, Марго, Валерия, Анна, Катя, Margo...) — обращайся СТРОГО в женском роде («чтобы ты знала», «ты не одна», «ты сама»).\n"
        "   - Если имя мужское (Михаил, Александр, Дмитрий, Илья...) — в мужском роде («чтобы ты знал», «ты не один»).\n"
        "   - Если род имени неясен — пиши нейтрально («чтобы ты знал(а)»).\n"
        "4. 💬 ЖИВОЕ ОБЩЕНИЕ: Используй уместные эмодзи (🤍, 😊, 🕊️, ✨)."
    )

    prompt_content = f"{user_name}: {user_prompt}"
    if web_results:
        prompt_content += f"\n\n[Свежие результаты поиска из интернета]:\n{web_results}"

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_content}
            ],
            temperature=0.7,
            max_tokens=150
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error generating AI reply via Groq: {e}")
        return "🤖 Извините, не удалось сформировать ответ. Попробуйте еще раз позже."
