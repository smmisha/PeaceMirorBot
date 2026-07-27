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
