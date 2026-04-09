"""
LinguaMax · Text-to-Speech модуль
Использует gTTS (бесплатно, без API ключа)
Опционально: ElevenLabs для высокого качества
"""

import io
import os
import logging

logger = logging.getLogger(__name__)

ELEVENLABS_KEY = os.getenv("ELEVENLABS_API_KEY")  # опционально


async def text_to_speech(text: str, lang: str = "en") -> bytes | None:
    """
    Конвертирует текст в аудио.
    Приоритет: ElevenLabs (если есть ключ) → gTTS (бесплатно)
    Возвращает байты OGG/MP3 или None если не удалось.
    """
    if ELEVENLABS_KEY:
        result = await _elevenlabs_tts(text)
        if result: return result

    return await _gtts(text, lang)


async def _gtts(text: str, lang: str = "en") -> bytes | None:
    """gTTS — бесплатный Google TTS, не требует API ключа."""
    try:
        import asyncio
        from gtts import gTTS

        def _generate():
            tts = gTTS(text=text, lang=lang, slow=False)
            buf = io.BytesIO()
            tts.write_to_fp(buf)
            buf.seek(0)
            return buf.read()

        audio_bytes = await asyncio.to_thread(_generate)
        return audio_bytes
    except ImportError:
        logger.warning("gTTS not installed. Run: pip install gTTS")
        return None
    except Exception as e:
        logger.error(f"gTTS error: {e}")
        return None


async def _elevenlabs_tts(text: str) -> bytes | None:
    """ElevenLabs TTS — высокое качество (требует API ключ)."""
    try:
        import httpx
        voice_id = "pNInz6obpgDQGcFmaJgB"  # Adam — академический голос
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": ELEVENLABS_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": "eleven_monolingual_v1",
                    "voice_settings": {"stability": 0.7, "similarity_boost": 0.8},
                },
            )
            if r.status_code == 200:
                return r.content
            logger.warning(f"ElevenLabs error: {r.status_code}")
            return None
    except Exception as e:
        logger.error(f"ElevenLabs error: {e}")
        return None
