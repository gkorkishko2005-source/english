"""
PolyGlotty · TTS + STT модуль
TTS: gTTS (бесплатно) или ElevenLabs (высокое качество)
STT: OpenAI Whisper API (произношение + анализ)
"""

import io
import os
import logging
import difflib
import html
import re
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

ELEVENLABS_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY     = os.getenv("OPENAI_API_KEY")  # для Whisper STT
TTS_PROVIDER   = (os.getenv("TTS_PROVIDER") or "auto").lower()
TTS_RATE       = os.getenv("TTS_RATE", "-12%")


# ══════════════════════════════════════════════════════════════════
#  Context-aware voice roles (актёры озвучки)
#  Каждая роль = свой голос для ElevenLabs И для Edge, плюс темп речи.
#  Маппинг роли подбирает фронтенд по уровню/теме урока:
#    teacher  — A0-A1, базовые диалоги: мягкий, понятный голос Учителя
#    casual   — сленг, идиомы, разговор: живой, быстрый голос Носителя
#    academic — сложная грамматика, правила: строгий, глубокий Профессор
#  Любой голос переопределяется ENV-переменными без правки кода.
# ══════════════════════════════════════════════════════════════════
TTS_ROLE_VOICES = {
    "teacher": {
        "eleven": os.getenv("TTS_ELEVEN_TEACHER",  "21m00Tcm4TlvDq8ikWAM"),  # Rachel — тёплая
        "edge_en": os.getenv("TTS_EDGE_TEACHER",   "en-GB-SoniaNeural"),
        "rate":    os.getenv("TTS_RATE_TEACHER",   "-14%"),   # медленнее, для новичков
    },
    "casual": {
        "eleven": os.getenv("TTS_ELEVEN_CASUAL",   "TxGEqnHWrfWFTfGW9XjX"),  # Josh — молодой
        "edge_en": os.getenv("TTS_EDGE_CASUAL",    "en-US-AriaNeural"),
        "rate":    os.getenv("TTS_RATE_CASUAL",    "+6%"),    # живее и быстрее
    },
    "academic": {
        "eleven": os.getenv("TTS_ELEVEN_ACADEMIC", "pNInz6obpgDQGcFmaJgB"),  # Adam — лекторский
        "edge_en": os.getenv("TTS_EDGE_ACADEMIC",  "en-GB-RyanNeural"),
        "rate":    os.getenv("TTS_RATE_ACADEMIC",  "-8%"),    # размеренно, авторитетно
    },
}
# "default" = текущее поведение (обратная совместимость).
TTS_ROLE_VOICES["default"] = {
    "eleven": os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB"),
    "edge_en": None,        # None → _edge_voice() выбирает по языку как раньше
    "rate": TTS_RATE,
}


def _role_cfg(role: str | None) -> dict:
    return TTS_ROLE_VOICES.get((role or "default").lower(), TTS_ROLE_VOICES["default"])


# ══════════════════════════════════════════════════════════════════
#  TTS — Text to Speech
# ══════════════════════════════════════════════════════════════════

async def text_to_speech(text: str, lang: str = "en", role: str | None = None) -> bytes | None:
    """Конвертирует текст в аудио. ElevenLabs → Edge neural voice → gTTS.
    `role` выбирает актёра озвучки (teacher / casual / academic / default)."""
    clean = _prepare_tts_text(text)
    if not clean:
        return None

    if ELEVENLABS_KEY and TTS_PROVIDER in ("auto", "elevenlabs"):
        result = await _elevenlabs_tts(clean, lang, role)
        if result: return result

    if TTS_PROVIDER in ("auto", "edge"):
        result = await _edge_tts(clean, lang, role)
        if result: return result

    return await _gtts(clean, lang)


def _prepare_tts_text(text: str) -> str:
    """Makes AI text sound less mechanical and prevents reading markup aloud."""
    text = html.unescape(str(text or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[*_`#>\[\]{}|~]+", " ", text)
    text = re.sub(r"\b(copy|copied|копировать|скопировано)\b", " ", text, flags=re.I)
    text = re.sub(r"([.!?]){2,}", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 700:
        cut = max(text.rfind(".", 0, 700), text.rfind("?", 0, 700), text.rfind("!", 0, 700))
        text = text[:cut + 1 if cut > 220 else 700].strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


async def _gtts(text: str, lang: str = "en") -> bytes | None:
    try:
        import asyncio
        from gtts import gTTS

        def _generate():
            tts = gTTS(text=text, lang=lang, slow=False)
            buf = io.BytesIO()
            tts.write_to_fp(buf)
            buf.seek(0)
            return buf.read()

        return await asyncio.to_thread(_generate)
    except ImportError:
        logger.warning("gTTS not installed: pip install gTTS")
        return None
    except Exception as e:
        logger.error(f"gTTS error: {e}")
        return None


def _edge_voice(lang: str = "en") -> str:
    base = (lang or "en").split("-")[0].lower()
    env_voice = os.getenv(f"TTS_VOICE_{base.upper()}") or os.getenv("TTS_VOICE")
    if env_voice:
        return env_voice
    return {
        "en": "en-GB-SoniaNeural",
        "ru": "ru-RU-SvetlanaNeural",
        "es": "es-ES-ElviraNeural",
        "pt": "pt-BR-FranciscaNeural",
    }.get(base, "en-GB-SoniaNeural")


async def _edge_tts(text: str, lang: str = "en", role: str | None = None) -> bytes | None:
    """Microsoft neural voices. Free fallback that is much more natural than gTTS."""
    try:
        import edge_tts

        cfg = _role_cfg(role)
        base = (lang or "en").split("-")[0].lower()
        # Role voices are English-only; for other languages keep the per-lang voice.
        voice = cfg["edge_en"] if (cfg.get("edge_en") and base == "en") else _edge_voice(lang)
        rate = cfg.get("rate") or TTS_RATE
        communicate = edge_tts.Communicate(
            text=text,
            voice=voice,
            rate=rate,
            volume="+0%",
        )
        audio = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                audio.extend(chunk["data"])
        return bytes(audio) if audio else None
    except ImportError:
        logger.info("edge-tts not installed; falling back to gTTS")
        return None
    except Exception as e:
        logger.warning(f"Edge TTS error: {e}")
        return None


async def _elevenlabs_tts(text: str, lang: str = "en", role: str | None = None) -> bytes | None:
    # Role-aware voice. ELEVENLABS_VOICE_ID (если задан) форсит один голос на всё.
    voice_id = os.getenv("ELEVENLABS_VOICE_ID") or _role_cfg(role)["eleven"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": ELEVENLABS_KEY, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
                    "voice_settings": {
                        "stability": 0.45,
                        "similarity_boost": 0.85,
                        "style": 0.25,
                        "use_speaker_boost": True,
                    },
                },
            )
            if r.status_code == 200: return r.content
            logger.warning(f"ElevenLabs {r.status_code}: {r.text[:160]}")
            return None
    except Exception as e:
        logger.error(f"ElevenLabs: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
#  STT — Whisper произношение
# ══════════════════════════════════════════════════════════════════

#  Частые галлюцинации Whisper на тишине/шуме — модель "додумывает" фразы.
_WHISPER_HALLUCINATIONS = {
    "thank you", "thank you.", "thanks for watching", "thank you for watching",
    "thank you for watching.", "thanks for watching.", "you", "you.",
    "please subscribe", "subscribe", ".", "bye", "bye.", "so", "so.",
    "thank you very much", "okay", "ok",
}


def _looks_like_hallucination(text: str) -> bool:
    """
    Определяет "словесный мусор" Whisper при пустой/тихой записи:
    короткие типовые фразы или зацикленное повторение одного токена.
    """
    if not text:
        return True
    t = text.strip()
    low = t.lower()
    if low in _WHISPER_HALLUCINATIONS:
        return True
    words = [w for w in re.split(r"\s+", low) if w]
    if not words:
        return True
    # Зацикленный бред: много слов, но почти все одинаковые (низкое разнообразие).
    if len(words) >= 4:
        uniq = len(set(words))
        if uniq / len(words) <= 0.3:
            return True
    return False


async def transcribe_audio(audio_bytes: bytes, filename: str = "audio.ogg", lang: str = "en") -> str | None:
    """
    Транскрибирует аудио.
    Приоритет: OpenAI Whisper (если есть ключ) → Google STT (бесплатно).
    `lang` — BCP-47 base (e.g. 'en','ru','es','pt'). Whisper auto-detects but
    accepts a hint; Google free STT needs explicit hint.
    Возвращает None, если аудио пустое/тихое (или Whisper вернул галлюцинацию),
    чтобы вызывающий код показал пользователю ошибку вместо «мусорной» оценки.
    """
    # Пустая/обрезанная запись — не тратим STT-запрос.
    if not audio_bytes or len(audio_bytes) < 2000:
        return None
    if OPENAI_KEY:
        result = await _whisper_transcribe(audio_bytes, filename, lang)
        if result and not _looks_like_hallucination(result):
            return result
    return await _google_stt(audio_bytes, lang, filename)


def _audio_mime(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    return {
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".mp4": "audio/mp4",
        ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
    }.get(ext, "application/octet-stream")


async def _whisper_transcribe(audio_bytes: bytes, filename: str, lang: str = "en") -> str | None:
    try:
        mime = _audio_mime(filename)
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
                files={"file": (filename, audio_bytes, mime)},
                data={"model": "whisper-1", "language": lang or "en"},
            )
            if r.status_code == 200:
                return r.json().get("text","").strip()
            logger.warning(f"Whisper STT {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"Whisper: {e}")
        return None


async def _google_stt(audio_bytes: bytes, lang: str = "en", filename: str = "audio.ogg") -> str | None:
    """Google Speech Recognition — бесплатно, без API ключа."""
    try:
        import asyncio
        import io
        import speech_recognition as sr

        def _recognize():
            recognizer = sr.Recognizer()
            lang_map = {"ru":"ru-RU","en":"en-US","es":"es-ES","pt":"pt-BR"}
            stt_lang = lang_map.get((lang or "en")[:2], "en-US")
            ext = Path(filename or "audio.ogg").suffix.lower().lstrip(".") or "ogg"
            if ext == "m4a":
                ext = "mp4"
            # Конвертируем браузерный формат в WAV через pydub/ffmpeg.
            try:
                from pydub import AudioSegment
                audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes), format=ext)
                wav_buf = io.BytesIO()
                audio_segment.export(wav_buf, format="wav")
                wav_buf.seek(0)
                with sr.AudioFile(wav_buf) as source:
                    audio = recognizer.record(source)
            except Exception:
                # Fallback: пробуем напрямую
                with sr.AudioFile(io.BytesIO(audio_bytes)) as source:
                    audio = recognizer.record(source)
            return recognizer.recognize_google(audio, language=stt_lang)

        return await asyncio.to_thread(_recognize)
    except ImportError:
        logger.warning("speech_recognition or pydub not installed")
        return None
    except Exception as e:
        logger.warning(f"Google STT: {e}")
        return None


def analyze_pronunciation(original: str, transcribed: str) -> dict:
    """
    Сравнивает оригинальную фразу с транскрипцией Whisper.
    Возвращает детальный анализ произношения.
    """
    orig_words   = original.lower().split()
    trans_words  = transcribed.lower().split()

    # Используем difflib для сравнения слов
    matcher = difflib.SequenceMatcher(None, orig_words, trans_words)
    ratio   = matcher.ratio()

    errors   = []
    opcodes  = matcher.get_opcodes()

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "replace":
            orig_chunk  = " ".join(orig_words[i1:i2])
            trans_chunk = " ".join(trans_words[j1:j2])
            errors.append({
                "type": "mispronounced",
                "original": orig_chunk,
                "heard":    trans_chunk,
            })
        elif tag == "delete":
            skipped = " ".join(orig_words[i1:i2])
            errors.append({
                "type": "skipped",
                "original": skipped,
                "heard":    "—",
            })
        elif tag == "insert":
            added = " ".join(trans_words[j1:j2])
            errors.append({
                "type": "added",
                "original": "—",
                "heard":    added,
            })

    score = int(ratio * 100)
    return {
        "score":       score,
        "errors":      errors,
        "transcribed": transcribed,
        "original":    original,
    }


def format_pronunciation_report(analysis: dict, lang: str = "ru") -> str:
    """Форматирует отчёт о произношении для Telegram."""
    score  = analysis["score"]
    errors = analysis["errors"]
    trans  = analysis["transcribed"]
    orig   = analysis["original"]

    if score >= 95:
        grade = "🏆 Отлично!" if lang=="ru" else "🏆 Excellent!"
        color = "🟢"
    elif score >= 80:
        grade = "👍 Хорошо!" if lang=="ru" else "👍 Good!"
        color = "🟡"
    elif score >= 60:
        grade = "💪 Почти!" if lang=="ru" else "💪 Getting there!"
        color = "🟠"
    else:
        grade = "🔄 Попробуй снова" if lang=="ru" else "🔄 Try again"
        color = "🔴"

    text = (
        f"🎙 <b>{'Анализ произношения' if lang=='ru' else 'Pronunciation Analysis'}</b>\n\n"
        f"{color} <b>{grade}</b> — {score}%\n\n"
        f"📝 <b>{'Оригинал' if lang=='ru' else 'Original'}:</b> <i>{orig}</i>\n"
        f"🎤 <b>{'Услышано' if lang=='ru' else 'Heard'}:</b> <i>{trans}</i>\n"
    )

    if errors:
        text += f"\n❌ <b>{'Ошибки:' if lang=='ru' else 'Issues:'}</b>\n"
        for e in errors[:5]:
            if e["type"] == "mispronounced":
                text += f"• <code>{e['original']}</code> → услышано как <code>{e['heard']}</code>\n"
            elif e["type"] == "skipped":
                text += f"• Пропущено: <code>{e['original']}</code>\n"
    else:
        text += f"\n✅ {'Все слова произнесены правильно!' if lang=='ru' else 'All words pronounced correctly!'}\n"

    return text
