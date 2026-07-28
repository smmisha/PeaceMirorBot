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
    """Fetches a 100% Russian, clean, non-profane, family-friendly meme photo URL and title."""
    import httpx, random, re
    from conflict_detector import find_violation
    subreddits = ["Pikabu", "ru_memes"]
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for _ in range(8):
                sub = random.choice(subreddits)
                resp = await client.get(f"https://meme-api.com/gimme/{sub}")
                if resp.status_code == 200:
                    data = resp.json()
                    url = data.get("url")
                    title = data.get("title", "🤪 Свежий мем")

                    # Check 1: Must NOT be NSFW / Spoiler
                    if data.get("nsfw", False) or data.get("spoiler", False):
                        continue

                    # Check 2: Must contain Russian Cyrillic text
                    if not re.search(r'[а-яёА-ЯЁ]', title):
                        continue

                    # Check 3: MUST BE 100% CLEAN WITHOUT PROFANITY OR BAD WORDS!
                    has_violation, _ = find_violation(title)
                    if has_violation:
                        continue

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


async def generate_ai_reply(user_prompt: str, user_name: str = "Участник", user_mention: Optional[str] = None) -> str:
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

    # HARDCODED ABSOLUTE SAFETY GUARDRAIL (Zero-tolerance for self-harm/suicide prompts)
    danger_keywords = [
        "суицид", "уйти из жизни", "умереть", "покончить с собой", "покончить собой",
        "вскрыть вен", "вскрыться", "повеситься", "спрыгнуть с", "убить себя",
        "не хочу жить", "зачем жить", "ухода из жизни"
    ]
    prompt_lower = user_prompt.lower()
    if any(dk in prompt_lower for dk in danger_keywords):
        logger.warning(f"CRITICAL SAFETY INTERCEPT: Self-harm keyword detected in prompt from user '{user_name}'!")
        target_tag = user_mention or user_name
        return (
            f"{target_tag}, твоя жизнь — это самое ценное, что есть! Пожалуйста, не думай о таком и не делай необдуманных шагов. "
            f"Всегда есть выход и люди, готовые помочь. Поговори с близкими или позвони на бесплатную линию поддержки: 8-800-200-01-22. "
            f"Ты не один, береги себя! 🤍"
        )

    target_tag = user_mention if user_mention else (f"@{user_name}" if not user_name.startswith("@") else user_name)

    system_prompt = (
        "Тебя зовут Мирчик. Ты — умный, душевный, с отличным чувством юмора и отзывчивый участник этого Telegram-чата.\n\n"
        "ТВОЙ ХАРАКТЕР И ПРАВИЛА ОБЩЕНИЯ:\n"
        "0. 🛑 БЕЗОПАСНОСТЬ И ЦЕННОСТЬ ЖИЗНИ (ВЫСШИЙ ПРИОРИТЕТ): Ни при каких обстоятельствах НЕ шути про самоповреждение или уход из жизни! Проявляй заботу.\n"
        "1. 🧠 УМНЫЙ БАЛАНС (ФАКТЫ VS БОЛТОВНЯ):\n"
        "   - Если у тебя спрашивают ФАКТЫ, ТОЧНЫЕ ДАННЫЕ, НАУКУ, МАТЕМАТИКУ или просят что-то назвать — ДАВАЙ ТОЧНЫЙ, УМНЫЙ И ПРАВИЛЬНЫЙ ОТВЕТ по существу!\n"
        "   - И ТОЛЬКО когда с тобой просто общаются / болтают о жизни — придумывай смешные бытовые истории на ходу.\n"
        "2. 💬 ЖИВОЙ ДИАЛОГ И ЛЁГКИЙ ЮМОР: В обычном разговоре не навязывай помощь и не лечи лекциями. Общайся легко и с душой!\n"
        "3. 🎨 ЭМОДЗИ И СТИЛЬ: Используй от 1 до МАКСИМУМ 4 уместных эмодзи на всё сообщение.\n"
        "4. 🚫 НЕ ПИШИ ТЕГИ И СОБАЧКИ @: Категорически ЗАПРЕЩЕНО вставлять теги со знаком @ (например, @username, @Ruslan). Программа Python прикрепит тег человека автоматически. В тексте ответа используй обычное имя или обращайся на «ты».\n"
        "5. 🇷🇺 ИСКЛЮЧИТЕЛЬНО ЧИСТЫЙ И ЕСТЕСТВЕННЫЙ РУССКИЙ ЯЗЫК:\n"
        "   - Отвечай СТРОГО на чистом, естественном русском языке! Категорически ЗАПРЕЩЕНО вставлять английские слова (listened, feeling, passing, with time) в середину русских предложений!\n"
        "   - 🚫 НИКАКИХ СКОБОК С ОКОНЧАНИЯМИ: Категорически ЗАПРЕЩЕНО писать корявые конструкции в скобках вроде «одна(й)», «один(на)», «сделал(а)»! Пиши чистую и естественную русскую речь («Ты не один», «Я рядом», «Ты справишься»)."
    )

    prompt_content = f"{user_name}: {user_prompt}"
    if web_results:
        prompt_content += f"\n\n[Свежие результаты поиска из интернета]:\n{web_results}"

    import asyncio, re
    def fix_mentions(txt: str) -> str:
        if not txt:
            return txt
        # Strip any stray @mentions generated by AI so Python attaches tag deterministically
        cleaned = re.sub(r'@[a-zA-Z0-9_\u0400-\u04FF]+', '', txt).strip()
        # Remove any leading punctuation artifact like comma or colon after removing tag
        cleaned = re.sub(r'^[,\s:]+', '', cleaned).strip()
        return cleaned

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt_content}
                ],
                temperature=0.85,
                max_tokens=600
            )
            raw_reply = response.choices[0].message.content.strip()
            return fix_mentions(raw_reply)
        except Exception as e:
            logger.warning(f"Groq API call attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                await asyncio.sleep(1)
            else:
                logger.error(f"Groq API unavailable after retries: {e}. Trying Mistral AI fallback...")

    # Fallback to Mistral AI API if Groq fails or is overloaded
    mistral_reply = await _call_mistral_fallback(system_prompt, prompt_content)
    if mistral_reply:
        return fix_mentions(mistral_reply)

    return "😴 Ой, Мирчик сейчас немного занят или отвлёкся на чай! Напиши мне через пару секунд ☕"


async def _call_mistral_fallback(system_prompt: str, prompt_content: str) -> Optional[str]:
    """Fallback AI call using Mistral AI API when Groq is unavailable or overloaded."""
    from config import MISTRAL_API_KEY
    if not MISTRAL_API_KEY:
        return None

    try:
        import httpx
        url = "https://api.mistral.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {MISTRAL_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "mistral-small-latest",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_content}
            ],
            "temperature": 0.85,
            "max_tokens": 600
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                reply = data["choices"][0]["message"]["content"].strip()
                logger.info("Successfully received fallback AI reply from Mistral AI!")
                return reply
            else:
                logger.warning(f"Mistral API returned status {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"Error executing Mistral AI fallback: {e}")
    return None


async def summarize_chat_history(history: list[dict]) -> str:
    """Summarizes chat history into structured highlights using Groq Llama 3.3 70B."""
    if not history:
        return "📝 В истории чата пока недостаточно сообщений для подведения итогов."

    formatted_history = "\n".join([f"[{item['time']}] {item['name']}: {item['text']}" for item in history[-50:]])

    system_prompt = (
        "Ты — идеальный суммаризатор общения в Telegram чате.\n"
        "Твоя задача — составить красивую, интересную краткую выжимку (итоги) того, о чём разговаривали участники СЕГОДНЯ (начиная с 00:00 и до текущего момента).\n\n"
        "Формат ответа:\n"
        "📝 **КРАТКАЯ СВОДКА ДНЯ (За сегодня с 00:00):**\n\n"
        "• 💬 **Главные темы**: (2-3 коротких пункта)\n"
        "• 💡 **Ключевые мысли/итоги**: (главные тезисы разговора)\n"
        "• 🕊️ **Атмосфера дня**: (коротко про настроение в чате)"
    )

    client = _get_groq_client()
    if not client:
        return "🤖 AI модуль временно недоступен."

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Вот последние сообщения из чата:\n\n{formatted_history}"}
            ],
            temperature=0.4,
            max_tokens=600,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error generating chat summary: {e}")
        return "😅 Упс, не удалось составить выжимку чата. Попробуйте чуть позже!"
