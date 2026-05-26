"""
LinguaMax ALEX v4
"""

import asyncio
import base64
import json
import logging
import os
import random
import re
from urllib.parse import quote

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BotCommand, BufferedInputFile, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    KeyboardButton, MenuButtonWebApp, Message,
    PhotoSize, ReplyKeyboardMarkup, Voice,
    WebAppInfo,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database import (
    db, db_init, get_user, get_lang, get_level, get_interests, get_profession,
    upsert_user, update_user, add_xp, update_streak,
    get_streak_count, get_xp, get_rank, LEVEL_ORDER,
    add_word, get_due_words, update_word_review, get_word_count,
    add_idiom, get_due_idioms,
    log_mistake, get_mistakes,
    log_session, get_full_stats,
    log_toefl, get_toefl_scores,
    save_interest, get_all_interests,
    track_complexity,
    start_story, get_active_story, update_story,
    log_tone,
    check_premium,
    apply_referral, get_referral_count,
)
from prompts import (
    build_system, INTEREST_TAG,
    ROLEPLAY_SCENARIOS, STORY_TYPES,
    LESSON_PROMPTS, VOCAB_PROMPTS,
    TEST_PROMPTS, TOEFL_PROMPTS, TALK_PROMPTS,
)
from tts import text_to_speech, transcribe_audio, analyze_pronunciation, format_pronunciation_report

load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
BOT_SECRET    = os.getenv("BOT_SECRET", "polyglotty_secret_2025")
RAILWAY_URL   = os.getenv("RAILWAY_PUBLIC_DOMAIN", "localhost:8080")
BOT_USERNAME  = (os.getenv("BOT_NAME", "PolyGlotty_bot") or "PolyGlotty_bot").lstrip("@")

# ══ ADMIN WHITELIST (admin tools only) ═══════════════════════════════════════
# Добавь сюда Telegram UID админов. Premium больше не выдаётся автоматически.
# Узнать свой UID: написать @userinfobot
ADMIN_IDS: set = {
    1738695057,
    5399839500,
    725259177,
    1241890707,
    1428437531,
}
# ══ PREMIUM PRICES (Telegram Stars) ══════════════════════════════════════════
# 3 плана с разными баффами
PREMIUM_PLANS = {
    "basic": {
        "stars": 650, "months": 1, "price_usd": 899,  # cents for card payments
        "label_ru": "Basic · 1 мес", "label_en": "Basic · 1 mo",
        "tier": "basic",
        "model": "sonnet4", "msg_limit": 45, "history": 35, "max_tokens": 700,
    },
    "pro": {
        "stars": 1600, "months": 1, "price_usd": 1999,
        "label_ru": "Pro · 1 мес", "label_en": "Pro · 1 mo",
        "tier": "pro",
        "model": "sonnet", "msg_limit": 110, "history": 55, "max_tokens": 1050,
    },
    "ultimate": {
        "stars": 4200, "months": 1, "price_usd": 4999,
        "label_ru": "Ultimate · 1 мес", "label_en": "Ultimate · 1 mo",
        "tier": "ultimate",
        "model": "opus", "msg_limit": 260, "history": 90, "max_tokens": 1300,
    },
    "basic_year": {
        "stars": int(650 * 12 * 0.85), "months": 12, "price_usd": int(899 * 12 * 0.85),
        "label_ru": "Basic · 1 год", "label_en": "Basic · 1 year",
        "tier": "basic",
        "model": "sonnet4", "msg_limit": 45, "history": 35, "max_tokens": 700,
    },
    "pro_year": {
        "stars": int(1600 * 12 * 0.85), "months": 12, "price_usd": int(1999 * 12 * 0.85),
        "label_ru": "Pro · 1 год", "label_en": "Pro · 1 year",
        "tier": "pro",
        "model": "sonnet", "msg_limit": 110, "history": 55, "max_tokens": 1050,
    },
    "ultimate_year": {
        "stars": int(4200 * 12 * 0.85), "months": 12, "price_usd": int(4999 * 12 * 0.85),
        "label_ru": "Ultimate · 1 год", "label_en": "Ultimate · 1 year",
        "tier": "ultimate",
        "model": "opus", "msg_limit": 260, "history": 90, "max_tokens": 1300,
    },
}
# First-time discount: 10% off
FIRST_TIME_DISCOUNT = 0.10
STRIPE_TOKEN = os.getenv("STRIPE_PROVIDER_TOKEN", "")
MODEL         = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

bot       = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp        = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

BOT_PROFILE = {
    "name_default": "PolyGlotty English",
    "name_ru": "PolyGlotty English",
    "short_default": "English tutor in Telegram: free A0-C2 course, ALEX chat with subscription, grammar, TOEFL and streaks.",
    "short_ru": "Репетитор английского в Telegram: бесплатный курс A0-C2, чат ALEX по подписке, грамматика, TOEFL и стрик.",
    "description_default": (
        "PolyGlotty is an AI English tutor inside Telegram.\n\n"
        "Practice English every day:\n"
        "• Free A0-C2 course, flashcards, paths, drills and listening\n"
        "• ALEX Chat with corrections with any subscription\n"
        "• Grammar games and spaced repetition\n"
        "• Pro roleplay: interview, travel, cafe, business\n"
        "• Ultimate TOEFL practice, progress and streaks\n\n"
        "Commands: /start, /premium, /share, /lesson, /vocab, /test, /toefl, /roleplay, /support, /terms.\n\n"
        "Open the app: the free course is available right away, ALEX Chat starts with a subscription."
    ),
    "description_ru": (
        "PolyGlotty — AI-репетитор английского прямо в Telegram.\n\n"
        "Практикуй английский каждый день:\n"
        "• бесплатный курс A0-C2, карточки, путь, drills и аудирование\n"
        "• чат ALEX с исправлением ошибок по любой подписке\n"
        "• grammar games и интервальное повторение\n"
        "• Pro-сценки: интервью, путешествия, кафе, бизнес\n"
        "• Ultimate TOEFL, прогресс и стрик\n\n"
        "Команды: /start, /premium, /share, /lesson, /vocab, /test, /toefl, /roleplay, /support, /terms.\n\n"
        "Открой приложение: бесплатный курс доступен сразу, чат ALEX начинается с подписки."
    ),
}

# ══════════════════════════════════════════════════════════════════
#  ИСТОРИЯ И СОСТОЯНИЯ
# ══════════════════════════════════════════════════════════════════

histories:   dict[int, list[dict]] = {}
waiting:     dict[int, str]        = {}
session_ctx: dict[int, dict]       = {}

def get_history(uid): return histories.setdefault(uid, [])
def add_message(uid, role, content):
    h = get_history(uid)
    h.append({"role": role, "content": content})
    if len(h) > 30: histories[uid] = h[-30:]
def clear_history(uid): histories[uid] = []
def set_ctx(uid, **kw): session_ctx.setdefault(uid, {}).update(kw)
def get_ctx(uid) -> dict: return session_ctx.get(uid, {})
def clear_ctx(uid): session_ctx.pop(uid, None)

FOOTER = "\n\n<i>──────────────────────────────────</i>\n<i>/lesson · /vocab · /test · /toefl · /roleplay · /story · /help</i>"

# ══════════════════════════════════════════════════════════════════
#  ANTHROPIC API
# ══════════════════════════════════════════════════════════════════

async def _call_anthropic(system: str, messages: list, max_tokens: int = 1500) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "prompt-caching-2024-07-31",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": max_tokens,
                # Prompt caching: до 90% скидки на input-токены при cache hits
                "system": [{"type": "text", "text": system,
                            "cache_control": {"type": "ephemeral"}}],
                "messages": messages,
            },
        )
        data = r.json()
        if "error" in data:
            raise Exception(data["error"].get("message","API error"))
        return data["content"][0]["text"].strip()


async def ask_alex(uid: int, user_text: str, mode: str = "general", extra: str = "") -> str:
    lang       = await get_lang(uid)
    level      = await get_level(uid)
    interests  = await get_interests(uid)
    profession = await get_profession(uid)

    add_message(uid, "user", user_text)
    system = build_system(level, lang, interests, profession, mode)
    if extra: system += f"\n\n{extra}"

    try:
        reply = await _call_anthropic(system, get_history(uid))
    except Exception as e:
        return f"⚠️ Error. Try again.\n<i>{str(e)[:80]}</i>"

    # Smart Interest Detection
    for interest in INTEREST_TAG.findall(reply):
        await save_interest(uid, interest.strip(), source="auto")
    clean = INTEREST_TAG.sub("", reply).strip()

    # Адаптивная сложность
    has_complex = any(p in user_text.lower() for p in [
        "although","however","therefore","furthermore","nevertheless",
        "consequently","whereas","provided that","in spite of","would have"
    ])
    if len(user_text.split()) > 20 or has_complex:
        level_change = await track_complexity(uid, True)
    elif len(user_text.split()) < 5:
        level_change = await track_complexity(uid, False)
    else:
        level_change = None

    add_message(uid, "assistant", clean)
    result = clean + FOOTER

    if level_change:
        direction, new_level = level_change.split(":")
        emoji = "🎉" if direction == "up" else "💡"
        msg = (f"\n\n{emoji} <b>Уровень изменён на {new_level}!</b>" if lang=="ru"
               else f"\n\n{emoji} <b>Level adjusted to {new_level}!</b>")
        result += msg

    return result


async def ask_alex_raw(prompt: str, system: str) -> str:
    try:
        return await _call_anthropic(system, [{"role": "user", "content": prompt}], max_tokens=2000)
    except Exception as e:
        logger.error(f"ask_alex_raw: {e}")
        return ""


async def analyze_photo_vision(uid: int, photo_bytes: bytes) -> str:
    lang       = await get_lang(uid)
    level      = await get_level(uid)
    interests  = await get_interests(uid)
    profession = await get_profession(uid)
    b64 = base64.standard_b64encode(photo_bytes).decode()
    prompt = (
        "Analyze this image for English language learning.\n"
        "Structure your response as:\n"
        "📸 <b>What I see:</b> [describe the image]\n"
        "💡 <b>Key English:</b> [5 most useful words/phrases]\n"
        "🎓 <b>Learning task:</b> [engaging exercise based on the image]\n\n"
        "If menu → roleplay ordering. If UI/interface → explain tech terms. If text → analyze grammar."
    )
    system = build_system(level, lang, interests, profession, "vision_context")
    try:
        reply = await _call_anthropic(system, [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": prompt},
        ]}])
        return reply + FOOTER
    except Exception as e:
        return f"⚠️ <i>{str(e)[:100]}</i>"

# ══════════════════════════════════════════════════════════════════
#  TOEFL LISTENING — лекции И диалоги
# ══════════════════════════════════════════════════════════════════

async def run_toefl_listening(uid: int, message: Message):
    lang  = await get_lang(uid)
    level = await get_level(uid)

    # Чередуем лекции и диалоги
    ctx  = get_ctx(uid)
    last = ctx.get("toefl_last_type","lecture")
    content_type = "dialogue" if last == "lecture" else "lecture"
    set_ctx(uid, toefl_last_type=content_type)

    type_label = "диалог" if (lang=="ru" and content_type=="dialogue") else (
        "dialogue" if content_type=="dialogue" else ("лекция" if lang=="ru" else "lecture")
    )

    status_msg = await message.answer(
        f"🎧 <b>Генерирую {type_label}...</b>" if lang=="ru" else f"🎧 <b>Generating {type_label}...</b>"
    )

    toefl_system = build_system(level, lang, mode="toefl_json")
    cmd = f"[TOEFL_GENERATE_{'DIALOGUE' if content_type=='dialogue' else 'LECTURE'}: {level}]"
    raw = await ask_alex_raw(cmd, toefl_system)

    try:
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not json_match: raise ValueError("No JSON")
        data = json.loads(json_match.group())
        transcript = data["transcript"]
        questions  = data["questions"]
        topic      = data.get("topic", "")
        setting    = data.get("setting", "")
    except Exception as e:
        logger.error(f"TOEFL parse error: {e}\nRaw: {raw[:300]}")
        await status_msg.edit_text("⚠️ Ошибка. Попробуй ещё раз." if lang=="ru" else "⚠️ Error. Try again.")
        return

    await status_msg.edit_text("🔊 <b>Синтезирую аудио...</b>" if lang=="ru" else "🔊 <b>Generating audio...</b>")

    audio_bytes = await text_to_speech(transcript)

    type_icon = "💬" if content_type == "dialogue" else "🎓"
    caption_detail = f"<i>{setting}</i>\n" if setting else ""

    if audio_bytes:
        af = BufferedInputFile(audio_bytes, filename=f"{content_type}.mp3")
        await message.answer_voice(
            af,
            caption=(
                f"{type_icon} <b>{'Диалог' if content_type=='dialogue' else 'Лекция'}: {topic}</b>\n"
                f"{caption_detail}"
                f"{'Прослушай внимательно. Когда закончишь — нажми кнопку.' if lang=='ru' else 'Listen carefully. When done — press the button.'}"
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Готов отвечать" if lang=="ru" else "✅ Ready for questions",
                    callback_data="toefl_q_start"
                )
            ]])
        )
    else:
        # Fallback: transcript для чтения
        read_label = "Прочитай этот диалог" if (lang=="ru" and content_type=="dialogue") else (
            "Прочитай эту лекцию" if lang=="ru" else
            f"Read this {content_type} carefully"
        )
        await message.answer(
            f"{type_icon} <b>{topic}</b>{(' — ' + setting) if setting else ''}\n\n"
            f"<i>{transcript}</i>\n\n"
            f"{'Нажми когда запомнишь:' if lang=='ru' else 'Press when ready:'}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Готов" if lang=="ru" else "✅ Ready",
                    callback_data="toefl_q_start"
                )
            ]])
        )

    set_ctx(uid, toefl_transcript=transcript, toefl_questions=questions,
            toefl_answers={}, toefl_q_idx=0, toefl_type=content_type)
    await status_msg.delete()
    log_session(uid, "toefl_listening")


async def send_toefl_question(uid: int, message: Message, idx: int):
    ctx       = get_ctx(uid)
    questions = ctx.get("toefl_questions", [])
    lang      = await get_lang(uid)
    if idx >= len(questions):
        await finish_toefl_listening(uid, message)
        return
    q = questions[idx]
    text = f"❓ <b>Question {idx+1}/{len(questions)}</b>\n\n{q['question_text']}"
    buttons = [[InlineKeyboardButton(text=f"{k}: {v[:60]}", callback_data=f"toefl_ans_{idx}_{k}")]
               for k, v in q["options"].items()]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def finish_toefl_listening(uid: int, message: Message):
    ctx        = get_ctx(uid)
    questions  = ctx.get("toefl_questions", [])
    answers    = ctx.get("toefl_answers", {})
    transcript = ctx.get("toefl_transcript", "")
    lang       = await get_lang(uid)
    level      = await get_level(uid)

    correct = sum(1 for i, q in enumerate(questions) if answers.get(str(i)) == q["correct_answer"])

    check_prompt = f"[TOEFL_CHECK_ANSWERS]\nTranscript: {transcript[:500]}\n"
    for i, q in enumerate(questions):
        check_prompt += f"Q{i+1}: {q['question_text']}\nUser: {answers.get(str(i),'?')} | Correct: {q['correct_answer']}\n"
        if answers.get(str(i)) != q["correct_answer"]:
            check_prompt += f"Explanation: {q.get('explanation','')}\n"

    toefl_system = build_system(level, lang, mode="toefl_json")
    analysis = await ask_alex_raw(check_prompt, toefl_system)

    await message.answer(
        f"🎧 <b>{'Результат' if lang=='ru' else 'Result'}</b>\n\n"
        f"✅ {'Правильно' if lang=='ru' else 'Correct'}: <b>{correct}/{len(questions)}</b>\n\n"
        f"{analysis[:3000]}"
    )
    log_toefl(uid, "listening", correct, len(questions))
    clear_ctx(uid)
    waiting.pop(uid, None)

# ══════════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════════

def main_kb(lang: str) -> ReplyKeyboardMarkup:
    ru = [["📚 Урок грамматики","📝 Словарь"],["🎭 Ролевой диалог","🎮 Story Quest"],
          ["✅ Тест","🎓 TOEFL"],["✍️ Проверить текст","🎨 Тон фразы"],
          ["💬 Разговор","⚔️ Дебаты"],["🗣 Идиомы","❌ Мои ошибки"],["📊 Прогресс",""]]
    en = [["📚 Grammar Lesson","📝 Vocabulary"],["🎭 Roleplay","🎮 Story Quest"],
          ["✅ Test","🎓 TOEFL"],["✍️ Check Writing","🎨 Tone Editor"],
          ["💬 Speaking","⚔️ Debate"],["🗣 Idioms","❌ My Mistakes"],["📊 Progress",""]]
    rows_data = ru if lang=="ru" else en
    rows = []
    for row in rows_data:
        btns = [KeyboardButton(text=t) for t in row if t]
        if btns: rows.append(btns)
    return ReplyKeyboardMarkup(
        keyboard=rows, resize_keyboard=True,
        input_field_placeholder="Пиши по-английски — ALEX проверит..." if lang=="ru" else "Write in English — ALEX will check...",
    )

def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
    ]])

def level_kb():
    rows = [[InlineKeyboardButton(text=lv, callback_data=f"setlevel_{lv}")] for lv in LEVEL_ORDER]
    rows.append([InlineKeyboardButton(text="🔍 Placement test", callback_data="test_placement")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _simple_kb(items: dict, lang: str, back: bool = True) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=v[0 if lang=="ru" else 1], callback_data=k)] for k,v in items.items()]
    if back: rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def roleplay_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=d["ru" if lang=="ru" else "en"], callback_data=k)]
        for k, d in ROLEPLAY_SCENARIOS.items()
    ])

def story_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=d["ru" if lang=="ru" else "en"], callback_data=k)]
        for k, d in STORY_TYPES.items()
    ])

def lesson_kb(lang):
    items = {
        "lesson_tenses":       ("⏰ Времена глагола","⏰ Verb Tenses"),
        "lesson_conditionals": ("🔀 Условные","🔀 Conditionals"),
        "lesson_modal":        ("💭 Модальные глаголы","💭 Modal Verbs"),
        "lesson_passive":      ("🔄 Пассивный залог","🔄 Passive Voice"),
        "lesson_articles":     ("📌 Артикли","📌 Articles"),
        "lesson_prepositions": ("📍 Предлоги","📍 Prepositions"),
        "lesson_phrasal":      ("🔗 Фразовые глаголы","🔗 Phrasal Verbs"),
        "lesson_reported":     ("💬 Косвенная речь","💬 Reported Speech"),
        "lesson_subjunctive":  ("🌙 Сослагательное","🌙 Subjunctive"),
        "lesson_inversion":    ("🔁 Инверсия C1-C2","🔁 Inversion C1-C2"),
    }
    return _simple_kb(items, lang)

def vocab_kb(lang):
    items = {
        "vocab_new":         ("🆕 Новые слова","🆕 New Words"),
        "vocab_review":      ("🔄 Повторение SM-2","🔄 SM-2 Review"),
        "vocab_flashcards":  ("🃏 Флэш-карточки","🃏 Flashcards"),
        "vocab_collocations":("🤝 Коллокации","🤝 Collocations"),
        "vocab_idioms_adv":  ("🗣 Продвинутые идиомы","🗣 Advanced Idioms"),
        "vocab_topic":       ("📂 По теме","📂 By Topic"),
        "daily_quiz":        ("📅 Ежедневный квиз","📅 Daily Quiz"),
        "idioms_cultural":   ("🎭 Культурные идиомы","🎭 Cultural Idioms"),
    }
    return _simple_kb(items, lang)

def test_kb(lang):
    items = {
        "test_grammar":   ("📐 Грамматика","📐 Grammar"),
        "test_vocab":     ("📝 Лексика","📝 Vocabulary"),
        "test_reading":   ("📖 Чтение","📖 Reading"),
        "test_writing":   ("✍️ Письмо","✍️ Writing"),
        "test_mixed":     ("🎲 Смешанный","🎲 Mixed"),
        "test_placement": ("🔍 Определить уровень","🔍 Placement Test"),
    }
    return _simple_kb(items, lang)

def toefl_kb(lang):
    items = {
        "toefl_reading":   ("📖 Reading","📖 Reading"),
        "toefl_listening": ("🎧 Listening + Audio","🎧 Listening + Audio"),
        "toefl_speaking1": ("🗣 Speaking Independent","🗣 Speaking Independent"),
        "toefl_speaking2": ("🗣 Speaking Integrated","🗣 Speaking Integrated"),
        "toefl_writing1":  ("✍️ Writing Independent","✍️ Writing Independent"),
        "toefl_writing2":  ("✍️ Writing Integrated","✍️ Writing Integrated"),
        "toefl_full":      ("🏆 Полный мини-тест","🏆 Full Mini-Test"),
        "toefl_strategy":  ("💡 Стратегии","💡 Strategies"),
        "toefl_score":     ("📊 Мои баллы","📊 My Scores"),
    }
    return _simple_kb(items, lang)

def talk_kb(lang):
    items = {
        "talk_daily":     ("☀️ Повседневная жизнь","☀️ Daily Life"),
        "talk_travel":    ("✈️ Путешествия","✈️ Travel"),
        "talk_work":      ("💼 Работа","💼 Work"),
        "talk_debate":    ("⚔️ Дебаты","⚔️ Debate"),
        "talk_business":  ("🤝 Бизнес English","🤝 Business English"),
        "talk_free":      ("💭 Свободная беседа","💭 Free Chat"),
        "talk_interview": ("👔 Mock Interview","👔 Mock Interview"),
    }
    return _simple_kb(items, lang)

def remind_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌅 08:00",callback_data="remind_08:00"),
         InlineKeyboardButton(text="☀️ 10:00",callback_data="remind_10:00")],
        [InlineKeyboardButton(text="🌞 12:00",callback_data="remind_12:00"),
         InlineKeyboardButton(text="🌇 18:00",callback_data="remind_18:00")],
        [InlineKeyboardButton(text="🌆 19:00",callback_data="remind_19:00"),
         InlineKeyboardButton(text="🌙 21:00",callback_data="remind_21:00")],
        [InlineKeyboardButton(text="❌ Disable",callback_data="remind_off")],
    ])

def flashcard_kb(word_id: int, lang: str):
    opts = [("😕 Не знал",1),("🤔 Почти",2),("😊 Помнил",4),("✅ Легко",5)] if lang=="ru" else [("😕 Forgot",1),("🤔 Hard",2),("😊 Good",4),("✅ Easy",5)]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=l,callback_data=f"fc_{word_id}_{q}") for l,q in opts]])

def shadowing_kb(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔊 Ещё раз" if lang=="ru" else "🔊 Hear again", callback_data="shadow_repeat"),
        InlineKeyboardButton(text="✍️ Написать" if lang=="ru" else "✍️ Write it", callback_data="shadow_write"),
    ]])

def pronunciation_kb(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎤 Записать голосовое" if lang=="ru" else "🎤 Record voice", callback_data="pronounce_record"),
        InlineKeyboardButton(text="✍️ Написать" if lang=="ru" else "✍️ Type instead", callback_data="shadow_write"),
    ]])

# ══════════════════════════════════════════════════════════════════
#  ПЛАНИРОВЩИК
# ══════════════════════════════════════════════════════════════════

MOTIVATION_LINES = {
    "ru": [
        "Начни сейчас: завтра легче не станет, но ты станешь сильнее.",
        "Пять минут сегодня лучше, чем идеальный план на потом.",
        "Двигайся дальше. Английский растёт от повторений, а не от ожидания.",
        "Одна новая фраза в день — и через месяц ты уже говоришь увереннее.",
        "Не жди мотивации. Сделай маленький шаг, и она появится по дороге.",
        "Пока кто-то откладывает, ты можешь стать на один урок ближе к цели.",
        "Сегодня не нужно идеально. Нужно просто продолжить.",
        "Будущий ты скажет спасибо за эти 5 минут практики.",
    ],
    "en": [
        "Start now: tomorrow will not get easier, but you will get stronger.",
        "Five minutes today beats a perfect plan for later.",
        "Keep moving. English grows through repetition, not waiting.",
        "One new phrase a day makes you noticeably more confident in a month.",
        "Do not wait for motivation. Take a small step and it will follow.",
        "While others postpone, you can get one lesson closer.",
        "It does not have to be perfect today. It just has to continue.",
        "Your future self will thank you for these five minutes.",
    ],
}

def motivation_line(lang: str = "ru") -> str:
    lines = MOTIVATION_LINES.get(lang) or MOTIVATION_LINES["en"]
    return random.choice(lines)

async def send_reminder(uid: int):
    user = await get_user(uid)
    if not user: return
    lang      = user.get("lang","ru")
    streak    = await get_streak_count(uid)
    interests = await get_interests(uid)
    due_cnt   = len(await get_due_words(uid, limit=10))
    due_idioms = len(await get_due_idioms(uid, limit=5))

    interest_hint = ""
    if interests:
        first = interests.split(",")[0].strip()
        interest_hint = f"\n💡 {'Сегодня разберём тему' if lang=='ru' else 'Today: topic'}: <b>{first}</b>"

    msgs_ru = ["<b>Время английского.</b>","<b>Твой ежедневный шаг ждёт.</b>","<b>Пора сделать английский чуть сильнее.</b>"]
    msgs_en = ["<b>Time for English.</b>","<b>Your daily step is waiting.</b>","<b>Make your English a little stronger today.</b>"]
    text = random.choice(msgs_ru if lang=="ru" else msgs_en) + interest_hint
    if streak > 2: text += f"\n\n🔥 Streak: <b>{streak}</b>!"
    if due_cnt:    text += f"\n📅 <b>{due_cnt}</b> {'слов для повторения' if lang=='ru' else 'words due'} → /vocab"
    if due_idioms: text += f"\n🗣 <b>{due_idioms}</b> {'идиом для повторения' if lang=='ru' else 'idioms due'} → /vocab"
    text += f"\n\n<i>{motivation_line(lang)}</i>"
    app_url = f"https://{RAILWAY_URL}" if RAILWAY_URL and "localhost" not in RAILWAY_URL else ""
    kb = None
    if app_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Открыть практику" if lang=="ru" else "Open practice",
                                  web_app=WebAppInfo(url=app_url))]
        ])
    try: await bot.send_message(uid, text, reply_markup=kb)
    except Exception as e: logger.warning(f"Reminder failed {uid}: {e}")

async def send_weekly_report(uid: int):
    stats = await get_full_stats(uid)
    lang  = await get_lang(uid)
    try:
        await bot.send_message(uid,
            f"📊 <b>{'Еженедельный отчёт' if lang=='ru' else 'Weekly Report'}</b>\n\n"
            f"🎯 {stats['level']} · {stats['rank']} · ⭐ {stats['xp']} XP\n"
            f"🔥 Streak: <b>{stats['streak']}</b> · Sessions: <b>{stats['sessions']}</b>\n"
            f"📝 Words: <b>{stats['words']}</b> · Tests: <b>{stats['tests']}</b>"
        )
    except Exception: pass

# ══ DAILY WORD PUSH ══════════════════════════════════════════════════════════
DAILY_WORDS = [
    {"word":"Perseverance","ph":"/ˌpɜːsɪˈvɪərəns/","tr":"Настойчивость","ex":"Success requires perseverance."},
    {"word":"Eloquent","ph":"/ˈeləkwənt/","tr":"Красноречивый","ex":"She gave an eloquent speech."},
    {"word":"Ambiguous","ph":"/æmˈbɪɡjuəs/","tr":"Двусмысленный","ex":"The instructions were ambiguous."},
    {"word":"Resilient","ph":"/rɪˈzɪliənt/","tr":"Устойчивый","ex":"Children are remarkably resilient."},
    {"word":"Procrastinate","ph":"/prəˈkræstɪneɪt/","tr":"Откладывать","ex":"Stop procrastinating and start working."},
    {"word":"Inevitable","ph":"/ɪnˈevɪtəbl/","tr":"Неизбежный","ex":"Change is inevitable in life."},
    {"word":"Comprehensive","ph":"/ˌkɒmprɪˈhensɪv/","tr":"Всесторонний","ex":"We need a comprehensive plan."},
    {"word":"Deteriorate","ph":"/dɪˈtɪəriəreɪt/","tr":"Ухудшаться","ex":"The weather began to deteriorate."},
    {"word":"Spontaneous","ph":"/spɒnˈteɪniəs/","tr":"Спонтанный","ex":"It was a spontaneous decision."},
    {"word":"Ubiquitous","ph":"/juːˈbɪkwɪtəs/","tr":"Повсеместный","ex":"Smartphones are now ubiquitous."},
    {"word":"Pragmatic","ph":"/præɡˈmætɪk/","tr":"Прагматичный","ex":"We need a pragmatic approach."},
    {"word":"Empathy","ph":"/ˈempəθi/","tr":"Эмпатия","ex":"Show empathy towards others."},
    {"word":"Hypothesis","ph":"/haɪˈpɒθəsɪs/","tr":"Гипотеза","ex":"We tested the hypothesis carefully."},
    {"word":"Notorious","ph":"/nəˈtɔːriəs/","tr":"Печально известный","ex":"The city is notorious for traffic."},
    {"word":"Meticulous","ph":"/mɪˈtɪkjʊləs/","tr":"Скрупулёзный","ex":"She is meticulous about details."},
    {"word":"Serendipity","ph":"/ˌserənˈdɪpɪti/","tr":"Счастливая случайность","ex":"Finding this book was pure serendipity."},
    {"word":"Juxtapose","ph":"/ˈdʒʌkstəpəʊz/","tr":"Сопоставлять","ex":"The artist juxtaposed light and dark."},
    {"word":"Epitome","ph":"/ɪˈpɪtəmi/","tr":"Воплощение","ex":"She is the epitome of elegance."},
    {"word":"Conundrum","ph":"/kəˈnʌndrəm/","tr":"Головоломка","ex":"This presents a real conundrum."},
    {"word":"Ephemeral","ph":"/ɪˈfemərəl/","tr":"Мимолётный","ex":"Fame can be ephemeral."},
    {"word":"Paradigm","ph":"/ˈpærədaɪm/","tr":"Парадигма","ex":"A paradigm shift in thinking."},
    {"word":"Aesthetic","ph":"/iːsˈθetɪk/","tr":"Эстетический","ex":"The room has a minimalist aesthetic."},
    {"word":"Dichotomy","ph":"/daɪˈkɒtəmi/","tr":"Дихотомия","ex":"The dichotomy between rich and poor."},
    {"word":"Candid","ph":"/ˈkændɪd/","tr":"Откровенный","ex":"Let me be candid with you."},
    {"word":"Tenacious","ph":"/tɪˈneɪʃəs/","tr":"Цепкий","ex":"She is tenacious in her pursuit."},
    {"word":"Anomaly","ph":"/əˈnɒməli/","tr":"Аномалия","ex":"The data showed an anomaly."},
    {"word":"Versatile","ph":"/ˈvɜːsətaɪl/","tr":"Универсальный","ex":"He is a versatile musician."},
    {"word":"Mundane","ph":"/mʌnˈdeɪn/","tr":"Обыденный","ex":"Escape from the mundane routine."},
    {"word":"Nuance","ph":"/ˈnjuːɑːns/","tr":"Нюанс","ex":"Appreciate the nuance of language."},
    {"word":"Catalyst","ph":"/ˈkætəlɪst/","tr":"Катализатор","ex":"The event was a catalyst for change."},
    {"word":"Idiosyncratic","ph":"/ˌɪdiəsɪŋˈkrætɪk/","tr":"Своеобразный","ex":"He has an idiosyncratic style."},
]

async def send_daily_word(uid: int):
    """Send daily word of the day to user."""
    import random
    lang = await get_lang(uid) or "ru"
    ru = lang == "ru"
    w = random.choice(DAILY_WORDS)
    try:
        from aiogram.types import WebAppInfo
        app_url = f"https://{RAILWAY_URL}" if RAILWAY_URL and "localhost" not in RAILWAY_URL else ""
        kb = None
        if app_url:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Учить в приложении" if ru else "📱 Learn in app",
                                      web_app=WebAppInfo(url=app_url))]
            ])
        await bot.send_message(uid,
            f"📚 <b>{'Слово дня' if ru else 'Word of the day'}</b>\n\n"
            f"🔤 <b>{w['word']}</b>\n"
            f"🔊 {w['ph']}\n"
            f"🇷🇺 {w['tr']}\n\n"
            f"📝 <i>{w['ex']}</i>\n\n"
            f"<b>{'Как использовать:' if ru else 'How to use it:'}</b> "
            f"{'напиши своё предложение с этим словом. В Basic ALEX исправит его в чате.' if ru else 'write your own sentence with this word. With Basic, ALEX will correct it in chat.'}\n\n"
            f"<i>{motivation_line(lang)}</i>",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception as e:
        logger.warning(f"daily_word to {uid}: {e}")

async def schedule_all():
    rows = await db("SELECT uid, remind_time FROM users WHERE remind_time IS NOT NULL AND remind_time != 'off'", fetch="all")
    if rows:
        for r in rows:
            try:
                h, m = map(int, r["remind_time"].split(":"))
                scheduler.add_job(send_reminder,"cron",hour=h,minute=m,args=[r["uid"]],id=f"remind_{r['uid']}",replace_existing=True)
            except Exception: pass
    all_users = await db("SELECT uid FROM users", fetch="all")
    if all_users:
        for r in all_users:
            scheduler.add_job(send_weekly_report,"cron",day_of_week="sun",hour=19,args=[r["uid"]],id=f"weekly_{r['uid']}",replace_existing=True)
            # Daily word at 9:00 AM
            scheduler.add_job(send_daily_word,"cron",hour=9,minute=0,args=[r["uid"]],id=f"daily_{r['uid']}",replace_existing=True)

# ══════════════════════════════════════════════════════════════════
#  INLINE MODE
# ══════════════════════════════════════════════════════════════════

@dp.inline_query()
async def handle_inline(query: InlineQuery):
    text = query.query.strip()
    if not text or len(text) < 2:
        await query.answer([], cache_time=1)
        return
    system = ('You are a compact translation assistant. Return ONLY valid JSON: '
              '{"translation":"...","explanation":"...","example":"..."} '
              'Translate to Russian if English input, or to English if Russian. No other text.')
    raw = await ask_alex_raw(text, system)
    try:
        data = json.loads(re.search(r'\{.*\}', raw, re.DOTALL).group())
    except Exception:
        data = {"translation": raw[:100], "explanation": "", "example": ""}
    results = [
        InlineQueryResultArticle(
            id="1", title=f"🔤 {data.get('translation','')}",
            description=data.get("explanation","")[:100],
            input_message_content=InputTextMessageContent(
                message_text=f"<b>{text}</b> → {data.get('translation','')}\n<i>{data.get('explanation','')}</i>\n📝 {data.get('example','')}",
                parse_mode="HTML"
            ),
        )
    ]
    await query.answer(results, cache_time=30)

# ══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ══════════════════════════════════════════════════════════════════

def user_display_name(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    full = " ".join(p for p in [getattr(user, "first_name", ""), getattr(user, "last_name", "")] if p)
    return full or "Learner"

async def setup_bot_profile():
    """Apply BotFather growth basics from prompt.rtf: searchable name, about text, commands, WebApp menu."""
    commands = [
        BotCommand(command="start", description="Open app / Главное меню"),
        BotCommand(command="premium", description="Plans and limits / Подписки"),
        BotCommand(command="share", description="Invite friends / Пригласить друзей"),
        BotCommand(command="lesson", description="Grammar lesson / Урок грамматики"),
        BotCommand(command="vocab", description="Vocabulary practice / Слова"),
        BotCommand(command="test", description="English test / Тест"),
        BotCommand(command="toefl", description="TOEFL practice / TOEFL"),
        BotCommand(command="roleplay", description="Roleplay scenarios / Сценки"),
        BotCommand(command="story", description="Interactive stories / Истории"),
        BotCommand(command="help", description="How to use / Помощь"),
        BotCommand(command="support", description="Support / Поддержка"),
        BotCommand(command="paysupport", description="Payment support / Оплата"),
        BotCommand(command="terms", description="Terms / Условия"),
        BotCommand(command="privacy", description="Privacy / Данные"),
    ]
    app_url = f"https://{RAILWAY_URL}" if RAILWAY_URL and "localhost" not in RAILWAY_URL else ""
    try:
        await bot.set_my_name(BOT_PROFILE["name_default"])
        await bot.set_my_name(BOT_PROFILE["name_ru"], language_code="ru")
        await bot.set_my_short_description(BOT_PROFILE["short_default"])
        await bot.set_my_short_description(BOT_PROFILE["short_ru"], language_code="ru")
        await bot.set_my_description(BOT_PROFILE["description_default"])
        await bot.set_my_description(BOT_PROFILE["description_ru"], language_code="ru")
        await bot.set_my_commands(commands)
        if app_url:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Open App", web_app=WebAppInfo(url=app_url)))
        logger.info("Bot profile metadata updated")
    except Exception as e:
        logger.warning(f"setup_bot_profile failed: {e}")

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid  = message.from_user.id
    name = user_display_name(message.from_user)

    try:
        await upsert_user(uid, name)
    except Exception as e:
        logger.error(f"upsert_user error: {e}")

    # Handle deep links
    args = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if args == "premium":
        try:
            await cmd_premium(message)
        except Exception as e:
            logger.error(f"cmd_premium from start error: {e}")
            await message.answer("⚠️ Error loading premium. Try /premium")
        return
    ref_applied = False
    if args.startswith("ref_"):
        try:
            ref_uid = int(args.replace("ref_",""))
            ref_applied = await apply_referral(uid, ref_uid)
        except Exception as e:
            logger.warning(f"referral failed uid={uid} args={args}: {e}")

    lang = "ru"
    try:
        lang = await get_lang(uid) or "ru"
    except Exception:
        pass
    ru = lang == "ru"

    # WebApp button
    app_url = f"https://{RAILWAY_URL}" if RAILWAY_URL and "localhost" not in RAILWAY_URL else ""

    kb_buttons = []
    if app_url:
        kb_buttons.append([InlineKeyboardButton(
            text="📱 Открыть приложение" if ru else "📱 Open App",
            web_app=WebAppInfo(url=app_url)
        )])
    kb_buttons.append([InlineKeyboardButton(
        text="💎 Premium",
        callback_data="open_premium"
    )])
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    share_text = (
        "AI-репетитор английского в Telegram: чат, ошибки, слова, TOEFL. Попробуй PolyGlotty"
        if ru else
        "AI English tutor in Telegram: chat, corrections, vocabulary, TOEFL. Try PolyGlotty"
    )
    kb_buttons.append([InlineKeyboardButton(
        text="Пригласить друга" if ru else "Invite a friend",
        url=f"https://t.me/share/url?url={quote(ref_link, safe='')}&text={quote(share_text, safe='')}"
    )])

    welcome_kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    is_prem = False
    try:
        is_prem = await check_premium(uid)
    except Exception:
        pass
    badge = " · 👑 Premium" if is_prem else ""

    ref_line = "\n\nБонус за приглашение начислен: +50 XP тебе, +150 XP другу." if (ru and ref_applied) else (
        "\n\nReferral bonus applied: +50 XP for you, +150 XP for your friend." if ref_applied else ""
    )
    text = (
        f"<b>Привет, {name}!</b>{badge}\n\n"
        f"<b>PolyGlotty</b> — AI-репетитор английского в Telegram.\n"
        f"Тренируй английский каждый день без отдельного приложения.\n\n"
        f"<b>Что умеет бот:</b>\n"
        f"• даёт бесплатный курс A0-C2, карточки, drills, аудирование и путь\n"
        f"• открывает живой чат с ALEX по любой подписке\n"
        f"• исправляет ошибки и объясняет грамматику\n"
        f"• открывает roleplay и проверку текста на Pro\n"
        f"• готовит к TOEFL на Ultimate и ведёт прогресс\n\n"
        f"<b>Старт:</b> открой приложение ниже. Бесплатно можно проходить курс и копить прогресс, чат с ALEX — по подписке."
        f"\n\n<i>{motivation_line('ru')}</i>"
        f"{ref_line}"
    ) if ru else (
        f"<b>Hey, {name}!</b>{badge}\n\n"
        f"<b>PolyGlotty</b> is an AI English tutor in Telegram.\n"
        f"Practice English every day without installing another app.\n\n"
        f"<b>What it does:</b>\n"
        f"• gives a free A0-C2 course, flashcards, drills, listening and paths\n"
        f"• unlocks live ALEX chat with any subscription\n"
        f"• corrects mistakes and explains grammar\n"
        f"• unlocks roleplay and text check on Pro\n"
        f"• helps with TOEFL on Ultimate and tracks progress\n\n"
        f"<b>Start:</b> open the app below. The free course is available, ALEX Chat starts with a subscription."
        f"\n\n<i>{motivation_line('en')}</i>"
        f"{ref_line}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=welcome_kb)

@dp.message(Command("share"))
async def cmd_share(message: Message):
    uid = message.from_user.id
    lang = await get_lang(uid) or "ru"
    ru = lang == "ru"
    ref_count = 0
    try:
        await upsert_user(uid, user_display_name(message.from_user))
        ref_count = await get_referral_count(uid)
    except Exception as e:
        logger.warning(f"share stats failed uid={uid}: {e}")
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    text = (
        "<b>Пригласи друга в PolyGlotty</b>\n\n"
        "Алгоритм простой: полезный бот + личная рекомендация = лучший рост.\n\n"
        f"Твоя ссылка:\n<code>{link}</code>\n\n"
        "Бонус: друг получает +50 XP, ты получаешь +150 XP за нового пользователя.\n"
        f"Приглашено: <b>{ref_count}</b>"
    ) if ru else (
        "<b>Invite a friend to PolyGlotty</b>\n\n"
        "Simple growth loop: useful bot + personal recommendation.\n\n"
        f"Your link:\n<code>{link}</code>\n\n"
        "Bonus: your friend gets +50 XP, you get +150 XP for a new user.\n"
        f"Invited: <b>{ref_count}</b>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Поделиться" if ru else "Share",
            url=f"https://t.me/share/url?url={quote(link, safe='')}&text={quote('AI English tutor in Telegram: chat, corrections, vocabulary, TOEFL. Try PolyGlotty', safe='')}"
        )]
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "open_premium")
async def cb_open_premium(cb: CallbackQuery):
    await cb.answer()
    await cmd_premium(cb.message)

@dp.message(Command("lang"))
async def cmd_lang(m: Message):
    await m.answer("🌍 Choose / Выбери:", reply_markup=lang_kb())

@dp.message(Command("level"))
async def cmd_level(m: Message):
    await m.answer("🎯 Choose your level:", reply_markup=level_kb())

@dp.message(Command("lesson"))
async def cmd_lesson(m: Message):
    lang = await get_lang(m.from_user.id)
    await m.answer("📚 <b>Grammar Lessons:</b>", reply_markup=lesson_kb(lang))

@dp.message(Command("vocab"))
async def cmd_vocab(m: Message):
    lang = await get_lang(m.from_user.id)
    await m.answer("📝 <b>Vocabulary:</b>", reply_markup=vocab_kb(lang))

@dp.message(Command("roleplay"))
async def cmd_roleplay(m: Message):
    lang = await get_lang(m.from_user.id)
    if not await has_access(m.from_user.id, "pro"):
        await send_upgrade_hint(m, "pro", lang, "Roleplay")
        return
    await m.answer("🎭 <b>Roleplay:</b>", reply_markup=roleplay_kb(lang))

@dp.message(Command("story"))
async def cmd_story(m: Message):
    lang = await get_lang(m.from_user.id)
    if not await has_access(m.from_user.id, "basic"):
        await send_upgrade_hint(m, "basic", lang, "Story Quest")
        return
    await m.answer("🎮 <b>Story Quest:</b>", reply_markup=story_kb(lang))

@dp.message(Command("debate"))
async def cmd_debate(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    if not await has_access(uid, "pro"):
        await send_upgrade_hint(m, "pro", lang, "Debate")
        return
    clear_history(uid)
    set_ctx(uid, debate_round=1)
    await bot.send_chat_action(m.chat.id, "typing")
    reply = await ask_alex(uid,
        "Start a debate exercise. Choose a controversial topic. Assign me a position. Explain rules briefly, then begin Round 1.",
        mode="debate"
    )
    await m.answer(reply)
    log_session(uid, "debate")
    waiting[uid] = "debate_active"

@dp.message(Command("test"))
async def cmd_test(m: Message):
    lang = await get_lang(m.from_user.id)
    await m.answer("✅ <b>Tests:</b>", reply_markup=test_kb(lang))

@dp.message(Command("toefl"))
async def cmd_toefl(m: Message):
    lang = await get_lang(m.from_user.id)
    if not await has_access(m.from_user.id, "ultimate"):
        await send_upgrade_hint(m, "ultimate", lang, "TOEFL")
        return
    await m.answer("🎓 <b>TOEFL iBT:</b>", reply_markup=toefl_kb(lang))

@dp.message(Command("talk"))
async def cmd_talk(m: Message):
    lang = await get_lang(m.from_user.id)
    await m.answer("💬 <b>Speaking:</b>", reply_markup=talk_kb(lang))

@dp.message(Command("tone"))
async def cmd_tone(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    await m.answer(
        "🎨 <b>{'Редактор тона фразы' if lang=='ru' else 'Tone Editor'}</b>\n\n"
        "{'Отправь любую фразу на английском — ALEX покажет 5 вариантов с разным стилем:' if lang=='ru' else 'Send any English phrase — ALEX will show 5 versions with different tones:'}\n"
        "👔 Professional · 🤝 Polite · 😤 Assertive · 😏 Passive-aggressive · 😎 Casual"
    )
    waiting[uid] = "tone_editor"

@dp.message(Command("shadow"))
async def cmd_shadow(m: Message):
    uid   = m.from_user.id
    lang  = await get_lang(uid)
    level = await get_level(uid)
    await bot.send_chat_action(m.chat.id, "typing")
    phrase = await ask_alex_raw(
        f"Give me ONE shadowing practice phrase for {level} level (10-20 words). Return ONLY the phrase.",
        "Return only the practice phrase, nothing else."
    )
    phrase = phrase.strip().strip('"').strip("'")
    set_ctx(uid, shadow_phrase=phrase)
    audio = await text_to_speech(phrase)
    if audio:
        await m.answer_voice(
            BufferedInputFile(audio,"phrase.mp3"),
            caption=f"🎙 <b>Shadowing</b>\n\n<i>{phrase}</i>\n\n{'Запиши голосовое или напиши:' if lang=='ru' else 'Record a voice message or type:'}",
            reply_markup=pronunciation_kb(lang)
        )
    else:
        await m.answer(f"🎙 <b>Shadowing</b>\n\n<i>{phrase}</i>", reply_markup=shadowing_kb(lang))
    waiting[uid] = "shadowing"
    log_session(uid, "shadowing")

@dp.message(Command("writing"))
async def cmd_writing(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    await m.answer(
        "✍️ <b>{'Проверка текста' if lang=='ru' else 'Writing Check'}</b>\n\n"
        "{'Отправь текст → получишь:' if lang=='ru' else 'Send text → get:'}\n"
        "✅ Corrected · 🌟 Native-like · 📚 Error breakdown"
    )
    waiting[uid] = "writing"

@dp.message(Command("sentence"))
async def cmd_sentence(m: Message):
    uid   = m.from_user.id
    level = await get_level(uid)
    await bot.send_chat_action(m.chat.id, "typing")
    reply = await ask_alex(uid, f"Give me a sentence builder exercise for {level}. 6-8 jumbled words. After I answer, confirm or show correct with explanation.", mode="grammar")
    await m.answer(reply)
    log_session(uid, "sentence_builder")
    waiting[uid] = "lesson_active"

@dp.message(Command("idioms"))
async def cmd_idioms(m: Message):
    uid   = m.from_user.id
    level = await get_level(uid)
    await bot.send_chat_action(m.chat.id, "typing")
    reply = await ask_alex(uid, f"Teach me 5 English idioms for {level}. Each: idiom, meaning, brief origin, 2 examples, when to use.", mode="vocab")
    await m.answer(reply)
    log_session(uid, "idioms")

@dp.message(Command("profession"))
async def cmd_profession(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    await m.answer(
        "💼 <b>{'Укажи свою профессию или сферу' if lang=='ru' else 'Enter your profession or field'}</b>\n\n"
        "{'Например:' if lang=='ru' else 'Example:'} <code>Java Developer</code>, <code>Digital Marketing</code>, <code>Teacher</code>\n\n"
        "{'ALEX будет создавать ролевые диалоги и примеры именно для твоей области.' if lang=='ru' else 'ALEX will create roleplays and examples specific to your field.'}"
    )
    waiting[uid] = "set_profession"

@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    uid   = m.from_user.id
    lang  = await get_lang(uid)
    stats = await get_full_stats(uid)
    xp    = stats["xp"]
    interests = await get_all_interests(uid)
    interest_line = ", ".join(r["interest"] for r in interests[:5]) if interests else ("нет" if lang=="ru" else "none")
    profession = await get_profession(uid)
    nxt_xp = {"🌱 Seedling":100,"📗 Beginner":300,"📘 Elementary":600,
               "📙 Pre-Intermediate":1000,"⭐ Intermediate":1500,
               "🌟 Upper-Intermediate":2500,"💫 Advanced":4000,"🏆 Master":9999}
    nxt = nxt_xp.get(stats["rank"],9999)
    bar = "█"*min(10,int(xp/nxt*10))+"░"*max(0,10-int(xp/nxt*10))
    await m.answer(
        f"📊 <b>{'Прогресс' if lang=='ru' else 'Progress'}</b>\n\n"
        f"🎯 {stats['level']} · {stats['rank']}\n"
        f"⭐ XP: <b>{xp}</b> [{bar}]\n\n"
        f"🔥 Streak: <b>{stats['streak']}</b> · Sessions: <b>{stats['sessions']}</b>\n"
        f"📝 Words: <b>{stats['words']}</b> · Tests: <b>{stats['tests']}</b>\n"
        f"❌ Errors: <b>{stats['errors']}</b> · TOEFL: <b>{stats['toefl']}</b>\n\n"
        + (f"💼 <i>{profession}</i>\n" if profession else "")
        + f"🎮 <i>{interest_line}</i>"
    )

@dp.message(Command("mistakes"))
async def cmd_mistakes(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    rows = await get_mistakes(uid, limit=10)
    if not rows:
        await m.answer("✅ No mistakes yet!" if lang=="en" else "✅ Ошибок пока нет!")
        return
    text = "❌ <b>Recent mistakes:</b>\n\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}. ❌ <code>{r['original'][:50]}</code>\n   ✅ <i>{r['corrected'][:50]}</i>\n   💡 {r['explanation'][:80]}\n\n"
    await m.answer(text)

@dp.message(Command("interests"))
async def cmd_interests(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    rows = await get_all_interests(uid)
    current = ", ".join(r["interest"] for r in rows) if rows else ("пусто" if lang=="ru" else "empty")
    await m.answer(
        f"🎮 <b>{'Интересы:' if lang=='ru' else 'Interests:'}</b> <i>{current}</i>\n\n"
        f"{'Напиши через запятую (ALEX также запоминает сам из разговора):' if lang=='ru' else 'Write comma-separated (ALEX also auto-saves from chat):'}\n"
        f"<code>gaming, music, travel, tech</code>"
    )
    waiting[uid] = "set_interests"

@dp.message(Command("remind"))
async def cmd_remind(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    text = (
        "<b>Ежедневное напоминание</b>\n\n"
        "Выбери время, когда ALEX будет мягко возвращать тебя к английскому.\n\n"
        f"<i>{motivation_line('ru')}</i>"
    ) if lang == "ru" else (
        "<b>Daily reminder</b>\n\n"
        "Choose when ALEX should bring you back to English practice.\n\n"
        f"<i>{motivation_line('en')}</i>"
    )
    await m.answer(text, reply_markup=remind_kb())

@dp.message(Command("help"))
async def cmd_help(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    app_url = f"https://{RAILWAY_URL}" if RAILWAY_URL and "localhost" not in RAILWAY_URL else ""
    kb = None
    if app_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📱 Открыть приложение" if lang=="ru" else "📱 Open App",
                                  web_app=WebAppInfo(url=app_url))],
            [InlineKeyboardButton(text="💎 Premium", callback_data="open_premium")],
        ])
    await m.answer(
        ("<b>ALEX — AI-репетитор английского</b>\n\n"
         "📱 Всё обучение в приложении:\n"
         "• Бесплатный курс A0-C2\n"
         "• Карточки и повторение\n"
         "• Grammar games\n"
         "• Story Mode\n"
         "• TOEFL практика\n"
         "• Чат с ALEX по подписке\n\n"
         "/premium — подписка\n"
         "/share — пригласить друга и получить XP\n"
         "/support — поддержка\n"
         "/terms — условия оплаты\n"
         "/start — главное меню\n\n"
         "👇 Открой приложение") if lang=="ru" else
        ("<b>ALEX — AI English Tutor</b>\n\n"
         "📱 All learning happens in the app:\n"
         "• Free A0-C2 course\n"
         "• Flashcards & review\n"
         "• Grammar games\n"
         "• Story Mode\n"
         "• TOEFL practice\n"
         "• ALEX chat with subscription\n\n"
         "/premium — subscription\n"
         "/share — invite a friend and earn XP\n"
         "/support — support\n"
         "/terms — payment terms\n"
         "/start — main menu\n\n"
         "👇 Open the app"),
        reply_markup=kb
    )

@dp.message(Command("support"))
async def cmd_support(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    ru = lang == "ru"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть приложение" if ru else "Open App",
                              web_app=WebAppInfo(url=f"https://{RAILWAY_URL}") if RAILWAY_URL and "localhost" not in RAILWAY_URL else None)]
    ]) if RAILWAY_URL and "localhost" not in RAILWAY_URL else None
    await m.answer(
        ("<b>Поддержка PolyGlotty</b>\n\n"
         "Если что-то не работает, напиши сюда одним сообщением:\n"
         "• что произошло;\n"
         "• твой тариф;\n"
         "• примерное время ошибки;\n"
         "• скриншот, если есть.\n\n"
         "По оплатам используй /paysupport.\n"
         "Обычно отвечаем вручную, поэтому лучше писать коротко и по делу.")
        if ru else
        ("<b>PolyGlotty Support</b>\n\n"
         "If something does not work, send one message here with:\n"
         "• what happened;\n"
         "• your plan;\n"
         "• approximate error time;\n"
         "• a screenshot if available.\n\n"
         "For payments, use /paysupport.\n"
         "Support is handled manually, so short clear reports help most."),
        reply_markup=kb
    )

@dp.message(Command("paysupport"))
async def cmd_paysupport(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    ru = lang == "ru"
    await m.answer(
        ("<b>Поддержка оплаты</b>\n\n"
         "Подписки оплачиваются через Telegram Stars. После успешной оплаты доступ обычно появляется сразу.\n\n"
         "Если доступ не появился:\n"
         "1. Перезапусти мини-приложение.\n"
         "2. Нажми /premium и проверь статус.\n"
         "3. Напиши сюда: тариф, время оплаты и скрин платежа.\n\n"
         "Возвраты и спорные платежи обрабатываются по правилам Telegram Stars.")
        if ru else
        ("<b>Payment Support</b>\n\n"
         "Subscriptions are paid through Telegram Stars. After a successful payment, access normally appears immediately.\n\n"
         "If access did not appear:\n"
         "1. Restart the mini app.\n"
         "2. Tap /premium and check your status.\n"
         "3. Send the plan, payment time and payment screenshot here.\n\n"
         "Refunds and disputed payments follow Telegram Stars rules.")
    )

@dp.message(Command("terms"))
async def cmd_terms(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    ru = lang == "ru"
    await m.answer(
        ("<b>Условия PolyGlotty</b>\n\n"
         "PolyGlotty даёт бесплатный доступ к курсу, карточкам, заданиям, пути и прогрессу.\n"
         "Подписка открывает ALEX Chat, AI-разборы, расширенные задания, модели и повышенные лимиты.\n\n"
         "Оплата: Telegram Stars.\n"
         "Период: 1 месяц или 1 год, в зависимости от выбранного плана.\n"
         "Доступ привязан к Telegram ID пользователя.\n"
         "AI-ответы могут ошибаться, поэтому важные учебные выводы стоит перепроверять.\n\n"
         "Поддержка: /support\n"
         "Оплата: /paysupport")
        if ru else
        ("<b>PolyGlotty Terms</b>\n\n"
         "PolyGlotty provides free access to the course, flashcards, tasks, learning path and progress.\n"
         "A subscription unlocks ALEX Chat, AI explanations, advanced tasks, models and higher limits.\n\n"
         "Payment: Telegram Stars.\n"
         "Period: 1 month or 1 year, depending on the selected plan.\n"
         "Access is linked to the user's Telegram ID.\n"
         "AI replies may be wrong, so important learning conclusions should be checked.\n\n"
         "Support: /support\n"
         "Payments: /paysupport")
    )

@dp.message(Command("privacy"))
async def cmd_privacy(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    ru = lang == "ru"
    await m.answer(
        ("<b>Данные и приватность</b>\n\n"
         "Мы сохраняем данные, которые нужны для обучения: Telegram ID, имя/ник, язык, уровень, XP, прогресс, подписку, карточки, ошибки и настройки обучения.\n"
         "Чат с ALEX используется для ответа и улучшения персонального контекста внутри продукта.\n"
         "Мы не продаём персональные данные.\n\n"
         "Чтобы запросить удаление данных, напиши /support и укажи свой Telegram ID.")
        if ru else
        ("<b>Data and Privacy</b>\n\n"
         "We store data required for learning: Telegram ID, name/username, language, level, XP, progress, subscription, flashcards, mistakes and learning settings.\n"
         "ALEX chat is used to answer and improve personal context inside the product.\n"
         "We do not sell personal data.\n\n"
         "To request deletion, contact /support and include your Telegram ID.")
    )

@dp.message(Command("reset"))
async def cmd_reset(m: Message):
    uid = m.from_user.id
    lang = await get_lang(uid)
    clear_history(uid); waiting.pop(uid,None); clear_ctx(uid)
    await m.answer("🔄 Reset." if lang=="en" else "🔄 Сброшен.")

# ══════════════════════════════════════════════════════════════════
#  CALLBACK
# ══════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = cb.data.replace("lang_","")
    await update_user(uid, lang=lang)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅")
    name = user_display_name(cb.from_user)
    await cb.message.answer(f"<b>{'Привет' if lang=='ru' else 'Hey'}, {name}!</b> 👋\n\nALEX. 🎯", reply_markup=main_kb(lang))
    await cb.message.answer("🎯 <b>Level:</b>", reply_markup=level_kb())

@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = await get_lang(uid)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Menu 👇", reply_markup=main_kb(lang))
    await cb.answer()

@dp.callback_query(F.data.startswith("setlevel_"))
async def cb_setlevel(cb: CallbackQuery):
    uid   = cb.from_user.id
    lang  = await get_lang(uid)
    level = cb.data.replace("setlevel_","")
    await update_user(uid, level=level)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(f"✅ {level}")
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, f"My English level is {level}. Give a brief encouraging welcome, what to focus on, and suggest what to start with today.", mode="general")
    await cb.message.answer(reply)

@dp.callback_query(F.data.startswith("rp_"))
async def cb_roleplay(cb: CallbackQuery):
    uid      = cb.from_user.id
    lang     = await get_lang(uid)
    if not await has_access(uid, "pro"):
        await cb.answer("Нужен Pro" if lang == "ru" else "Pro required", show_alert=True)
        await send_upgrade_hint(cb.message, "pro", lang, "Roleplay")
        return
    scenario = ROLEPLAY_SCENARIOS.get(cb.data)
    if not scenario: await cb.answer(); return
    if cb.data == "rp_custom":
        await cb.answer()
        await cb.message.answer("🎭 Опиши ситуацию:" if lang=="ru" else "🎭 Describe the scenario:")
        waiting[uid] = "rp_custom"
        return
    if cb.data == "rp_smart":
        # Динамический ролплей по профессии
        await cb.answer()
        profession = await get_profession(uid)
        if not profession:
            await cb.message.answer("💼 " + ("Сначала укажи профессию: /profession" if lang=="ru" else "First set your profession: /profession"))
            return
        level = await get_level(uid)
        clear_history(uid)
        await bot.send_chat_action(cb.message.chat.id, "typing")
        prompt = (
            f"Create a professional roleplay for a {profession}. "
            f"Design a realistic, challenging scenario where the student (who is a {profession}) "
            f"must communicate in English. Use professional jargon for this field. "
            f"Level: {level}. Start immediately in character. "
            f"At the end give detailed feedback on professional language use."
        )
        reply = await ask_alex(uid, prompt, mode="roleplay")
        await cb.message.answer(reply)
        log_session(uid, "roleplay_smart")
        waiting[uid] = "roleplay_active"
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    clear_history(uid)
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, scenario["prompt"], mode="roleplay")
    await cb.message.answer(reply)
    log_session(uid, f"roleplay_{cb.data}")
    waiting[uid] = "roleplay_active"

@dp.callback_query(F.data.startswith("story_"))
async def cb_story(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = await get_lang(uid)
    if not await has_access(uid, "basic"):
        await cb.answer("Нужна подписка" if lang == "ru" else "Subscription required", show_alert=True)
        await send_upgrade_hint(cb.message, "basic", lang, "Story Quest")
        return
    data = STORY_TYPES.get(cb.data)
    if not data: await cb.answer(); return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    clear_history(uid)
    await start_story(uid, cb.data)
    set_ctx(uid, story_hp=100, story_score=0)
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, data["prompt"], mode="story")
    await cb.message.answer(reply)
    log_session(uid, f"story_{cb.data}")
    waiting[uid] = "story_active"

@dp.callback_query(F.data.startswith("lesson_"))
async def cb_lesson(cb: CallbackQuery):
    uid    = cb.from_user.id
    prompt = LESSON_PROMPTS.get(cb.data,"")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="grammar")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "lesson_active"

@dp.callback_query(F.data.startswith("vocab_") | F.data.in_(["daily_quiz","idioms_cultural"]))
async def cb_vocab(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = await get_lang(uid)

    if cb.data == "idioms_cultural":
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer()
        await bot.send_chat_action(cb.message.chat.id, "typing")
        reply = await ask_alex(uid, "Generate a cultural idioms learning scenario with a short story rich in idioms, then ask me to identify 3 highlighted idioms.", mode="idioms_cultural")
        await cb.message.answer(reply)
        log_session(uid, "idioms_cultural")
        waiting[uid] = "vocab_active"
        return

    if cb.data in ("vocab_review","daily_quiz"):
        due = await get_due_words(uid, limit=5)
        if not due:
            await cb.answer()
            await cb.message.answer("✅ No words due today!" if lang=="en" else "✅ Нет слов для повторения!")
            return
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer()
        word = due[0]
        set_ctx(uid, review_queue=due, review_idx=0)
        await cb.message.answer(
            f"🃏 <b>Card 1/{len(due)}</b>\n\n📖 <b>{word['word']}</b>\n\n<i>{word['example']}</i>\n\n"
            f"{'Помнишь перевод?' if lang=='ru' else 'Remember the translation?'}",
            reply_markup=flashcard_kb(word["id"], lang)
        )
        return

    prompt = VOCAB_PROMPTS.get(cb.data,"")
    if not prompt: await cb.answer(); return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="vocab")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "vocab_active"

@dp.callback_query(F.data.startswith("fc_"))
async def cb_flashcard(cb: CallbackQuery):
    uid     = cb.from_user.id
    lang    = await get_lang(uid)
    parts   = cb.data.split("_")
    word_id = int(parts[1]); quality = int(parts[2])
    await update_word_review(word_id, quality)
    await add_xp(uid, 3)
    ctx   = get_ctx(uid)
    queue = ctx.get("review_queue",[])
    idx   = ctx.get("review_idx",0) + 1
    set_ctx(uid, review_idx=idx)
    await cb.message.edit_reply_markup(reply_markup=None)
    word_row = await db("SELECT * FROM vocabulary WHERE id=?", word_id, fetch="one")
    if word_row:
        await cb.message.answer(f"✅ <b>{word_row['word']}</b> = {word_row['translation']}\n<i>{word_row['example']}</i>")
    if idx < len(queue):
        word = queue[idx]
        await cb.message.answer(
            f"🃏 <b>Card {idx+1}/{len(queue)}</b>\n\n📖 <b>{word['word']}</b>\n\n<i>{word['example']}</i>",
            reply_markup=flashcard_kb(word["id"], lang)
        )
    else:
        await cb.message.answer(f"✅ Done! +{len(queue)*3} XP 🎉")
        log_session(uid, "vocab_review")
        clear_ctx(uid)
    await cb.answer()

# TOEFL — специфичные хэндлеры ПЕРВЫЕ
@dp.callback_query(F.data == "toefl_q_start")
async def cb_toefl_q_start(cb: CallbackQuery):
    uid  = cb.from_user.id
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅")
    await bot.send_chat_action(cb.message.chat.id, "typing")
    await asyncio.sleep(0.3)
    await send_toefl_question(uid, cb.message, 0)
    set_ctx(uid, toefl_q_idx=0)
    waiting[uid] = "toefl_listening_answers"

@dp.callback_query(F.data.startswith("toefl_ans_"))
async def cb_toefl_answer(cb: CallbackQuery):
    uid   = cb.from_user.id
    parts = cb.data.split("_")
    q_idx = int(parts[2]); answer = parts[3]
    ctx   = get_ctx(uid)
    answers = ctx.get("toefl_answers",{}); answers[str(q_idx)] = answer
    set_ctx(uid, toefl_answers=answers)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(f"✅ {answer}")
    next_idx = q_idx + 1
    questions = ctx.get("toefl_questions",[])
    if next_idx < len(questions):
        await bot.send_chat_action(cb.message.chat.id, "typing")
        await send_toefl_question(uid, cb.message, next_idx)
        set_ctx(uid, toefl_q_idx=next_idx)
    else:
        await bot.send_chat_action(cb.message.chat.id, "typing")
        await finish_toefl_listening(uid, cb.message)

@dp.callback_query(F.data.startswith("toefl_"))
async def cb_toefl(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = await get_lang(uid)
    if cb.data == "toefl_score":
        await cb.answer()
        rows = await get_toefl_scores(uid)
        if not rows: await cb.message.answer("🎓 No scores yet!"); return
        text = "🎓 <b>TOEFL Scores:</b>\n\n"
        for r in rows: text += f"📌 <b>{r['section']}</b>: best {r['best']}, avg {r['avg_s']:.0f} ({r['cnt']} sessions)\n"
        await cb.message.answer(text); return
    if not await has_access(uid, "ultimate"):
        await cb.answer("Нужен Ultimate" if lang == "ru" else "Ultimate required", show_alert=True)
        await send_upgrade_hint(cb.message, "ultimate", lang, "TOEFL")
        return
    if cb.data == "toefl_listening":
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer()
        await run_toefl_listening(uid, cb.message)
        return
    prompt = TOEFL_PROMPTS.get(cb.data,"")
    if not prompt: await cb.answer(); return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="toefl")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "toefl_active"

@dp.callback_query(F.data.startswith(("test_","talk_")))
async def cb_test_talk(cb: CallbackQuery):
    uid    = cb.from_user.id
    all_p  = {**TEST_PROMPTS, **TALK_PROMPTS}
    mode   = "test" if cb.data.startswith("test_") else "speaking"
    prompt = all_p.get(cb.data,"")
    if not prompt: await cb.answer(); return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode=mode)
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "test_active" if mode=="test" else "speaking_active"

@dp.callback_query(F.data.startswith("shadow_") | F.data == "pronounce_record")
async def cb_shadow(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = await get_lang(uid)
    if cb.data == "shadow_repeat":
        phrase = get_ctx(uid).get("shadow_phrase","")
        if phrase:
            audio = await text_to_speech(phrase)
            if audio: await cb.message.answer_voice(BufferedInputFile(audio,"phrase.mp3"), caption=f"🔊 <i>{phrase}</i>")
            else: await cb.message.answer(f"🔊 <i>{phrase}</i>")
    elif cb.data in ("shadow_write","pronounce_record"):
        await cb.message.answer("✍️ Write the phrase:" if lang=="en" else "✍️ Напиши или запиши голосовое:")
        waiting[uid] = "shadowing"
    await cb.answer()

@dp.callback_query(F.data.startswith("remind_"))
async def cb_remind(cb: CallbackQuery):
    uid  = cb.from_user.id
    data = cb.data.replace("remind_","")
    if data == "off":
        await update_user(uid, remind_time="off")
        if scheduler.get_job(f"remind_{uid}"): scheduler.remove_job(f"remind_{uid}")
        lang = await get_lang(uid) or "ru"
        await cb.answer(); await cb.message.edit_text("Reminders disabled." if lang=="en" else "Напоминания выключены.")
    else:
        lang = await get_lang(uid) or "ru"
        await update_user(uid, remind_time=data)
        try:
            h, m = map(int, data.split(":"))
            scheduler.add_job(send_reminder,"cron",hour=h,minute=m,args=[uid],id=f"remind_{uid}",replace_existing=True)
        except Exception as e: logger.warning(e)
        await cb.answer(f"✅ {data}")
        await cb.message.edit_text(
            (f"<b>Reminder set: {data}</b>\n\n<i>{motivation_line('en')}</i>" if lang=="en"
             else f"<b>Напоминание: {data}</b>\n\n<i>{motivation_line('ru')}</i>")
        )

# ══════════════════════════════════════════════════════════════════
#  ФОТО — Vision Learning
# ══════════════════════════════════════════════════════════════════

@dp.message(F.photo)
async def handle_photo(message: Message):
    lang = await get_lang(message.from_user.id) or "ru"
    await message.answer(
        "📸 Фото-анализ доступен в приложении! Нажми 📱 App внизу." if lang=="ru"
        else "📸 Photo analysis is available in the app! Tap 📱 App below."
    )

@dp.message(F.voice)
async def handle_voice(message: Message):
    lang = await get_lang(message.from_user.id) or "ru"
    await message.answer(
        "🎤 Голосовые сообщения обрабатываются в приложении! Нажми 📱 App внизу." if lang=="ru"
        else "🎤 Voice messages are processed in the app! Tap 📱 App below."
    )

# ══════════════════════════════════════════════════════════════════
#  ТЕКСТ
# ══════════════════════════════════════════════════════════════════

MENU_RU = {
    "📚 Урок грамматики":"lesson","📝 Словарь":"vocab","🎭 Ролевой диалог":"roleplay",
    "🎮 Story Quest":"story","✅ Тест":"test","🎓 TOEFL":"toefl",
    "✍️ Проверить текст":"writing","🎨 Тон фразы":"tone",
    "💬 Разговор":"talk","⚔️ Дебаты":"debate","🗣 Идиомы":"idioms","❌ Мои ошибки":"mistakes","📊 Прогресс":"stats",
}
MENU_EN = {
    "📚 Grammar Lesson":"lesson","📝 Vocabulary":"vocab","🎭 Roleplay":"roleplay",
    "🎮 Story Quest":"story","✅ Test":"test","🎓 TOEFL":"toefl",
    "✍️ Check Writing":"writing","🎨 Tone Editor":"tone",
    "💬 Speaking":"talk","⚔️ Debate":"debate","🗣 Idioms":"idioms","❌ My Mistakes":"mistakes","📊 Progress":"stats",
}

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    """Lightweight handler - redirect to WebApp, no AI calls."""
    uid   = message.from_user.id
    lang  = await get_lang(uid) or "ru"
    text  = message.text.strip()
    state = waiting.get(uid,"")

    # Handle pending states (no AI, just data saving)
    if state == "set_interests":
        waiting.pop(uid, None)
        for interest in [i.strip() for i in text.split(",") if i.strip()]:
            await save_interest(uid, interest, source="manual")
        await update_user(uid, interests=text[:200])
        await message.answer(f"✅ {'Сохранено!' if lang=='ru' else 'Saved!'} <i>{text}</i>")
        return

    if state == "set_profession":
        waiting.pop(uid, None)
        await update_user(uid, profession=text[:100])
        await save_interest(uid, text[:50], source="profession")
        await message.answer(f"💼 {'Профессия сохранена:' if lang=='ru' else 'Profession saved:'} <b>{text}</b>")
        return

    # Menu buttons → redirect to app
    menu = MENU_RU if lang=="ru" else MENU_EN
    if text in menu:
        pass  # fall through to redirect

    # Any active state → clear it and redirect
    if state:
        waiting.pop(uid, None)

    # Redirect to WebApp
    from aiogram.types import WebAppInfo
    app_url = f"https://{RAILWAY_URL}" if RAILWAY_URL and "localhost" not in RAILWAY_URL else ""
    kb = None
    if app_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📱 Открыть приложение" if lang=="ru" else "📱 Open App",
                                  web_app=WebAppInfo(url=app_url))],
            [InlineKeyboardButton(text="💎 Premium", callback_data="open_premium")],
        ])
    await message.answer(
        ("📱 Учись в приложении — Free даёт карточки, drills, игры и прогресс. ALEX Chat открывается по подписке.\n\n"
         "👇 Нажми кнопку ниже") if lang=="ru" else
        ("📱 Learn in the app — Free includes flashcards, drills, games and progress. ALEX Chat starts with a subscription.\n\n"
         "👇 Tap the button below"),
        reply_markup=kb
    )

@dp.message(Command("app"))
async def cmd_app(m: Message):
    """Открывает WebApp."""
    uid  = m.from_user.id
    lang = await get_lang(uid)
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    if not domain:
        await m.answer("⚠️ WebApp URL not configured." if lang=="en" else "⚠️ WebApp ещё не настроен.")
        return
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    await m.answer(
        "📱 <b>LinguaMax App</b>\n\n"
        + ("Открывай приложение — там твой прогресс, флэш-карточки и статистика!" if lang=="ru"
           else "Open the app — your progress, flashcards and stats!"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🚀 Открыть приложение" if lang=="ru" else "🚀 Open App",
                web_app=WebAppInfo(url=f"https://{domain}")
            )
        ]])
    )


@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    """Получает данные от WebApp и выполняет действие."""
    uid  = message.from_user.id
    lang = await get_lang(uid)
    try:
        data   = json.loads(message.web_app_data.data)
        action = data.get("action", "")
    except Exception:
        return

    action_map = {
        "lesson":     cmd_lesson,
        "vocab":      cmd_vocab,
        "toefl":      cmd_toefl,
        "roleplay":   cmd_roleplay,
        "remind":     cmd_remind,
        "mistakes":   cmd_mistakes,
        "interests":  cmd_interests,
        "profession": cmd_profession,
        "reset":      cmd_reset,
    }

    if action == "set_lang":
        new_lang = data.get("lang", "ru")
        await update_user(uid, lang=new_lang)
        await message.answer("✅ Language updated!" if new_lang=="en" else "✅ Язык обновлён!")
        return

    if action == "rate_card":
        word_id = data.get("word_id")
        quality = data.get("quality", 3)
        if word_id:
            from database import update_word_review, add_xp
            await update_word_review(word_id, quality)
            await add_xp(uid, 3)
        return

    if action == "add_word":
        await message.answer(
            "📝 Напиши слово которое хочешь добавить:" if lang=="ru"
            else "📝 Write the word you want to add:"
        )
        waiting[uid] = "vocab_active"
        return

    handler = action_map.get(action)
    if handler:
        await handler(message)


# ══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════

async def is_admin(uid: int) -> bool:
    """Check if user can use admin-only maintenance commands."""
    return uid in ADMIN_IDS

def tier_level(tier: str) -> int:
    return {"free": 0, "basic": 1, "pro": 2, "ultimate": 3}.get((tier or "free").lower(), 0)

async def get_access_tier(uid: int) -> str:
    try:
        from database import get_premium_info
        info = await get_premium_info(uid)
        return info.get("tier") if info.get("is_premium") else "free"
    except Exception:
        return "basic" if await check_premium(uid) else "free"

async def has_access(uid: int, tier: str) -> bool:
    return tier_level(await get_access_tier(uid)) >= tier_level(tier)

async def send_upgrade_hint(message: Message, tier: str, lang: str, feature: str):
    text = (
        f"<b>{feature}</b> доступно с тарифа <b>{tier.title()}</b>.\n\n"
        "В Free остаются карточки, drills, задания дня и прогресс. Подписка открывает живой ALEX Chat и продвинутые тренировки."
        if lang == "ru" else
        f"<b>{feature}</b> starts with <b>{tier.title()}</b>.\n\n"
        "Free still includes flashcards, drills, daily tasks and progress. Subscription unlocks live ALEX Chat and advanced practice."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="ALEX Subscriptions", callback_data="open_premium")]])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

async def grant_premium_via_server(uid: int, months: int, tier: str = "ultimate"):
    """Call server API to grant premium."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"http://localhost:8080/api/premium/grant",
                json={"uid": uid, "months": months, "tier": tier, "secret": BOT_SECRET}
            )
    except Exception as e:
        logger.error(f"grant_premium_via_server error: {e}")

# ══ /premium COMMAND ══════════════════════════════════════════════════════════
@dp.message(Command("premium"))
async def cmd_premium(msg: Message):
    uid = msg.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"

    # Check if already premium
    is_prem = await check_premium(uid)
    prem_badge = ""
    if is_prem:
        prem_badge = ("\n\n✅ <i>У тебя уже есть Premium. Можешь продлить!</i>" if ru
                      else "\n\n✅ <i>You already have Premium. You can extend!</i>")

    # Check if first-time buyer for discount
    is_first = True
    try:
        from database import get_premium_info
        info = await get_premium_info(uid)
        if info.get("tier"):  # has or had premium before
            is_first = False
    except Exception:
        pass

    discount_text = ""
    if is_first:
        discount_text = "\n\n🎁 <b>-10% на первую покупку!</b>" if ru else "\n\n🎁 <b>10% off your first purchase!</b>"
    year_text = "\n📅 <b>Годовой план: -15%</b>" if ru else "\n📅 <b>Annual plan: 15% off</b>"

    # Prices with discount
    b_stars = int(PREMIUM_PLANS["basic"]["stars"] * (1 - FIRST_TIME_DISCOUNT)) if is_first else PREMIUM_PLANS["basic"]["stars"]
    p_stars = int(PREMIUM_PLANS["pro"]["stars"] * (1 - FIRST_TIME_DISCOUNT)) if is_first else PREMIUM_PLANS["pro"]["stars"]
    u_stars = int(PREMIUM_PLANS["ultimate"]["stars"] * (1 - FIRST_TIME_DISCOUNT)) if is_first else PREMIUM_PLANS["ultimate"]["stars"]
    by_stars = PREMIUM_PLANS["basic_year"]["stars"]
    py_stars = PREMIUM_PLANS["pro_year"]["stars"]
    uy_stars = PREMIUM_PLANS["ultimate_year"]["stars"]

    kb_rows = [
        [InlineKeyboardButton(
            text=f"Basic — {b_stars} ⭐/мес (~$9)" if ru else f"Basic — {b_stars} ⭐/mo (~$9)",
            callback_data=f"prem_buy:basic:{'d' if is_first else 'n'}"
        )],
        [InlineKeyboardButton(
            text=f"Pro — {p_stars} ⭐/мес (~$20)" if ru else f"Pro — {p_stars} ⭐/mo (~$20)",
            callback_data=f"prem_buy:pro:{'d' if is_first else 'n'}"
        )],
        [InlineKeyboardButton(
            text=f"Ultimate — {u_stars} ⭐/мес (~$50)" if ru else f"Ultimate — {u_stars} ⭐/mo (~$50)",
            callback_data=f"prem_buy:ultimate:{'d' if is_first else 'n'}"
        )],
        [InlineKeyboardButton(
            text=f"Basic год — {by_stars} ⭐ (-15%)" if ru else f"Basic yearly — {by_stars} ⭐ (-15%)",
            callback_data="prem_buy:basic_year:n"
        )],
        [InlineKeyboardButton(
            text=f"Pro год — {py_stars} ⭐ (-15%)" if ru else f"Pro yearly — {py_stars} ⭐ (-15%)",
            callback_data="prem_buy:pro_year:n"
        )],
        [InlineKeyboardButton(
            text=f"Ultimate год — {uy_stars} ⭐ (-15%)" if ru else f"Ultimate yearly — {uy_stars} ⭐ (-15%)",
            callback_data="prem_buy:ultimate_year:n"
        )],
    ]
    # Add card payment option if Stripe is configured
    if STRIPE_TOKEN:
        kb_rows.append([InlineKeyboardButton(
            text="💳 Оплатить картой" if ru else "💳 Pay by card",
            callback_data="prem_card_menu"
        )])

    plans_kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    text = (
        "<b>ALEX Subscriptions</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Basic</b> — {b_stars} ⭐/мес\n"
        "├ 45 quota points/день\n"
        "├ Живой чат с ALEX\n"
        "├ Модели: Haiku 4.5 или Sonnet 4\n"
        "├ Haiku = 1 point, Sonnet 4 = 4 points\n"
        "├ Голосовые ответы + AI-подсказки\n"
        "├ 20 карточек / 5 часов и полный разбор правил\n"
        "└ Комфортный режим без пустого ожидания\n\n"
        f"<b>Pro</b> — {p_stars} ⭐/мес\n"
        "├ 110 quota points/день\n"
        "├ Модели: Haiku 4.5, Sonnet 4 или Sonnet 4.6\n"
        "├ Haiku = 1, Sonnet 4 = 4, Sonnet 4.6 = 5 points\n"
        "├ Roleplay, проверка текста, анализ ошибок\n"
        "├ 50 карточек / 4 часа и персональные drills\n"
        "└ Расширенные истории, сценарии и отчёты\n\n"
        f"<b>Ultimate</b> — {u_stars} ⭐/мес\n"
        "├ 260 quota points/день\n"
        "├ Модели: Haiku 4.5, Sonnet 4/4.6, Opus 4.1/4.7\n"
        "├ Haiku = 1, Sonnet = 4-5, Opus = 12-14 points\n"
        "├ TOEFL, персональный план и сертификаты\n"
        "├ 100 карточек / 4 часа и максимум аудио\n"
        "└ Длинная история диалога\n\n"
        f"<b>Год:</b> Basic {by_stars} ⭐ · Pro {py_stars} ⭐ · Ultimate {uy_stars} ⭐\n"
        "<i>Quota points защищают тарифы от перерасхода и держат подписки честными.</i>"
        f"{discount_text}{year_text}\n"
        f"{prem_badge}\n\n"
        "⭐ Stars или 💳 карта"
    ) if ru else (
        "<b>ALEX Subscriptions</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Basic</b> — {b_stars} ⭐/mo\n"
        "├ 45 quota points/day\n"
        "├ Live ALEX chat\n"
        "├ Models: Haiku 4.5 or Sonnet 4\n"
        "├ Haiku = 1 point, Sonnet 4 = 4 points\n"
        "├ Voice replies + AI hints\n"
        "├ 20 cards / 5 hours and full rule breakdowns\n"
        "└ Comfortable learning without empty waiting\n\n"
        f"<b>Pro</b> — {p_stars} ⭐/mo\n"
        "├ 110 quota points/day\n"
        "├ Models: Haiku 4.5, Sonnet 4 or Sonnet 4.6\n"
        "├ Haiku = 1, Sonnet 4 = 4, Sonnet 4.6 = 5 points\n"
        "├ Roleplay, text check and error analysis\n"
        "├ 50 cards / 4 hours and personal drills\n"
        "└ More stories, scenarios and reports\n\n"
        f"<b>Ultimate</b> — {u_stars} ⭐/mo\n"
        "├ 260 quota points/day\n"
        "├ Models: Haiku 4.5, Sonnet 4/4.6, Opus 4.1/4.7\n"
        "├ Haiku = 1, Sonnet = 4-5, Opus = 12-14 points\n"
        "├ TOEFL, personal plan and certificates\n"
        "├ 100 cards / 4 hours and max audio practice\n"
        "└ Long chat history\n\n"
        f"<b>Yearly:</b> Basic {by_stars} ⭐ · Pro {py_stars} ⭐ · Ultimate {uy_stars} ⭐\n"
        "<i>Quota points keep premium limits fair and sustainable.</i>"
        f"{discount_text}{year_text}\n"
        f"{prem_badge}\n\n"
        "⭐ Stars or 💳 card"
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=plans_kb)

# ══ PREMIUM BUY CALLBACK (Stars) ══════════════════════════════════════════
@dp.callback_query(F.data.startswith("prem_buy:"))
async def cb_prem_buy(cb: CallbackQuery):
    from aiogram.types import LabeledPrice
    parts = cb.data.split(":")
    plan_id = parts[1] if len(parts) > 1 else "basic"
    has_discount = (parts[2] == "d") if len(parts) > 2 else False
    plan = PREMIUM_PLANS.get(plan_id)
    if not plan:
        await cb.answer("Invalid plan", show_alert=True)
        return

    uid = cb.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"
    label = plan["label_ru"] if ru else plan["label_en"]

    stars = plan["stars"]
    if has_discount:
        stars = int(stars * (1 - FIRST_TIME_DISCOUNT))

    period = "1 год" if (ru and plan["months"] == 12) else "1 year" if plan["months"] == 12 else "1 месяц" if ru else "1 month"
    desc = f"ALEX Subscriptions {plan['tier'].upper()} — {period}"

    await cb.answer()
    try:
        await bot.send_invoice(
            chat_id=uid,
            title=f"ALEX Subscriptions {label}",
            description=desc,
            payload=f"premium:{plan_id}:{uid}",
            currency="XTR",
            prices=[LabeledPrice(label=f"Premium {label}", amount=stars)],
            protect_content=False,
        )
    except Exception as e:
        logger.error(f"send_invoice error: {e}")
        err = "Ошибка при создании платежа. Попробуй позже." if ru else "Payment error. Try again later."
        await bot.send_message(uid, err)

# ══ CARD PAYMENT MENU ════════════════════════════════════════════════════
@dp.callback_query(F.data == "prem_card_menu")
async def cb_card_menu(cb: CallbackQuery):
    uid = cb.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"
    await cb.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Basic — $8.99/мес" if ru else "Basic — $8.99/mo", callback_data="prem_card:basic")],
        [InlineKeyboardButton(text=f"Pro — $19.99/мес" if ru else "Pro — $19.99/mo", callback_data="prem_card:pro")],
        [InlineKeyboardButton(text=f"Ultimate — $49.99/мес" if ru else "Ultimate — $49.99/mo", callback_data="prem_card:ultimate")],
        [InlineKeyboardButton(text=f"Basic год — $91.69 (-15%)" if ru else "Basic yearly — $91.69 (-15%)", callback_data="prem_card:basic_year")],
        [InlineKeyboardButton(text=f"Pro год — $203.89 (-15%)" if ru else "Pro yearly — $203.89 (-15%)", callback_data="prem_card:pro_year")],
        [InlineKeyboardButton(text=f"Ultimate год — $509.89 (-15%)" if ru else "Ultimate yearly — $509.89 (-15%)", callback_data="prem_card:ultimate_year")],
    ])
    await cb.message.answer("💳 " + ("Выбери план для оплаты картой:" if ru else "Choose a plan to pay by card:"), reply_markup=kb)

@dp.callback_query(F.data.startswith("prem_card:"))
async def cb_card_buy(cb: CallbackQuery):
    from aiogram.types import LabeledPrice
    plan_id = cb.data.split(":")[1]
    plan = PREMIUM_PLANS.get(plan_id)
    if not plan or not STRIPE_TOKEN:
        await cb.answer("Card payments not configured" if not STRIPE_TOKEN else "Invalid plan", show_alert=True)
        return

    uid = cb.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"
    label = plan["label_ru"] if ru else plan["label_en"]
    price_usd = plan["price_usd"]  # in cents

    await cb.answer()
    try:
        await bot.send_invoice(
            chat_id=uid,
            title=f"ALEX Subscriptions {label}",
            description=f"ALEX Subscriptions {plan['tier'].upper()} — {('1 год' if ru else '1 year') if plan['months']==12 else ('1 месяц' if ru else '1 month')}",
            payload=f"premium:{plan_id}:{uid}",
            provider_token=STRIPE_TOKEN,
            currency="USD",
            prices=[LabeledPrice(label=f"Premium {label}", amount=price_usd)],
            protect_content=False,
        )
    except Exception as e:
        logger.error(f"card invoice error: {e}")
        await bot.send_message(uid, "⚠️ " + ("Ошибка. Попробуй оплату через Stars." if ru else "Error. Try Stars payment."))

# ══ PRE-CHECKOUT ══════════════════════════════════════════════════════════
@dp.pre_checkout_query()
async def pre_checkout(pcq):
    await bot.answer_pre_checkout_query(pcq.id, ok=True)

# ══ SUCCESSFUL PAYMENT ════════════════════════════════════════════════════
@dp.message(F.successful_payment)
async def on_payment_success(msg: Message):
    payload = msg.successful_payment.invoice_payload
    uid = msg.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"

    try:
        parts = payload.split(":")
        plan_id = parts[1] if len(parts) > 1 else "basic"
        plan = PREMIUM_PLANS.get(plan_id, PREMIUM_PLANS["basic"])
        months = plan["months"]
        label = plan["label_ru"] if ru else plan["label_en"]
        tier = plan.get("tier", "basic")
        tier_emoji = {"basic":"🟢","pro":"🔵","ultimate":"💎"}.get(tier,"🟢")

        # Grant premium in database via server
        await grant_premium_via_server(uid, months, tier)

        # Confirm to user
        text = (
            f"🎉 <b>Оплата прошла!</b>\n\n"
            f"{tier_emoji} ALEX Subscriptions <b>{tier.upper()}</b> активирован на <b>{label}</b>\n\n"
            f"Открой приложение — все функции разблокированы!\n\n"
            f"Спасибо за поддержку! 🙏"
        ) if ru else (
            f"🎉 <b>Payment successful!</b>\n\n"
            f"{tier_emoji} ALEX Subscriptions <b>{tier.upper()}</b> activated for <b>{label}</b>\n\n"
            f"Open the app — all features unlocked!\n\n"
            f"Thank you for your support! 🙏"
        )
        await msg.answer(text, parse_mode="HTML")
        logger.info(f"✅ Premium granted: uid={uid} plan={plan_id} months={months}")

    except Exception as e:
        logger.error(f"Payment success handler error: {e}")
        await msg.answer("✅ Payment received! Premium activated." if not ru else "✅ Оплата получена! Premium активирован.")

# ══ /admin COMMAND (grant free premium to anyone) ═════════════════════════
@dp.message(Command("admin_grant"))
async def cmd_admin_grant(msg: Message):
    """Only admins can use this to grant free premium to someone."""
    if msg.from_user.id not in ADMIN_IDS:
        return  # Silently ignore

    parts = msg.text.split()
    if len(parts) < 2:
        await msg.answer("Usage: /admin_grant <uid> [months]\nExample: /admin_grant 123456789 12")
        return

    try:
        target_uid = int(parts[1])
        months = int(parts[2]) if len(parts) > 2 else 1
        await grant_premium_via_server(target_uid, months)
        await msg.answer(f"✅ Granted {months} months of Premium to uid {target_uid}")
    except Exception as e:
        await msg.answer(f"❌ Error: {e}")

async def main():
    await db_init()
    await schedule_all()
    scheduler.start()
    logger.info("🎓 LinguaMax ALEX v4 Ultimate — запущен!")

    # Запускаем веб-сервер (для WebApp)
    from server import start_server
    web_runner = await start_server()

    await bot.delete_webhook(drop_pending_updates=True)
    await setup_bot_profile()

    try:
        await dp.start_polling(bot)
    finally:
        await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
