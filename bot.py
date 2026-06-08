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
import html
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
OFFICIAL_CHANNEL_URL = os.getenv("OFFICIAL_CHANNEL_URL", "https://t.me/polyglotty_daily").strip()

# Cache-bust the WebApp URL by appending the index.html mtime as ?v=.
# Telegram caches WebView contents per URL, and "no-cache" headers alone
# aren't always honoured by the Telegram client. Changing the URL on
# every deploy guarantees the WebView fetches the new HTML.
def _webapp_version() -> str:
    try:
        from pathlib import Path as _P
        p = _P(__file__).resolve().parent / "webapp" / "index.html"
        if p.exists():
            return str(int(p.stat().st_mtime))
    except Exception:
        pass
    return "1"

def webapp_url() -> str:
    if not RAILWAY_URL or "localhost" in RAILWAY_URL:
        return ""
    return f"https://{RAILWAY_URL}/?v={_webapp_version()}"
SUPPORT_USER_ID = int(os.getenv("SUPPORT_USER_ID", "8702782202") or "8702782202")
SUPPORT_USERNAME = (os.getenv("SUPPORT_USERNAME", "") or "").lstrip("@")

def support_contact_url() -> str:
    return f"https://t.me/{SUPPORT_USERNAME}" if SUPPORT_USERNAME else f"tg://user?id={SUPPORT_USER_ID}"

def public_bot_url() -> str:
    return f"https://t.me/{BOT_USERNAME}"

def referral_url(uid: int) -> str:
    return f"{public_bot_url()}?start=ref_{uid}"

def channel_url() -> str:
    return OFFICIAL_CHANNEL_URL or public_bot_url()

def channel_handle() -> str:
    """Returns "@PolyGlottyDailyEnglish" extracted from OFFICIAL_CHANNEL_URL.
    Used inside plain-text share copy where a t.me/... URL would
    collide with the preview Telegram renders for the ref link."""
    try:
        path = (OFFICIAL_CHANNEL_URL or "").rstrip("/").split("/")[-1]
        return f"@{path}" if path else ""
    except Exception:
        return ""

def html_link(url: str, text: str, bold: bool = False) -> str:
    label = html.escape(text, quote=False)
    href = html.escape(url, quote=True)
    link = f'<a href="{href}">{label}</a>'
    return f"<b>{link}</b>" if bold else link

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
TEST_PAYMENT_USER_IDS: set = {
    int(x) for x in os.getenv("TEST_PAYMENT_USER_IDS", "8702782202").split(",")
    if x.strip().isdigit()
}

def is_test_payment_user(uid: int) -> bool:
    return uid in ADMIN_IDS or uid in TEST_PAYMENT_USER_IDS

# ══ PREMIUM PRICES (Telegram Stars) ══════════════════════════════════════════
# Legacy ALEX subscription plans. Active invoices still use Telegram Stars only.
PREMIUM_PLANS = {
    "basic": {
        "stars": 650, "months": 1, "price_usd": 899,
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

# ══ MODULAR BILLING (split: Platform sub + ALEX credits) ─────────────────────
# Platform subscription unlocks course/exams/analytics/UI. Sold flat.
# ALEX credits are a separate pre-paid pool spent per chat message.
# Legacy PREMIUM_PLANS above stays alive for grandfathered renewals.
PLATFORM_PLANS = {
    "plat_1m":  {"stars": 299,  "period": "1m",       "starter_credits": 30,  "label_ru": "Платформа · 1 мес",   "label_en": "Platform · 1 mo"},
    "plat_6m":  {"stars": 1290, "period": "6m",       "starter_credits": 150, "label_ru": "Платформа · 6 мес",   "label_en": "Platform · 6 mo"},
    "plat_life":{"stars": 4990, "period": "lifetime", "starter_credits": 500, "label_ru": "Платформа · навсегда","label_en": "Platform · lifetime"},
}
CREDIT_PACKS = {
    "credits_100":  {"stars": 149,  "credits": 100,  "label_ru": "100 кредитов ALEX",    "label_en": "100 ALEX credits"},
    "credits_500":  {"stars": 599,  "credits": 500,  "label_ru": "500 кредитов ALEX",    "label_en": "500 ALEX credits"},
    "credits_2000": {"stars": 1990, "credits": 2000, "label_ru": "2 000 кредитов ALEX",  "label_en": "2 000 ALEX credits"},
}

def plan_stars_for_user(plan_id: str, uid: int, has_discount: bool = False) -> int:
    plan = PREMIUM_PLANS[plan_id]
    if plan_id == "basic" and is_test_payment_user(uid):
        return 1
    stars = int(plan["stars"])
    if has_discount:
        stars = int(stars * (1 - FIRST_TIME_DISCOUNT))
    return max(1, stars)

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
        "Free: A0-C2 course, flashcards, drills, listening, growth tree.\n"
        "Subscription: live ALEX chat, roleplay, text check, TOEFL · IELTS · CAE prep. Top up with ALEX credits.\n\n"
        "Commands: /start, /premium, /share, /lesson, /vocab, /test, /toefl, /roleplay, /support, /terms, /rules."
    ),
    "description_ru": (
        "PolyGlotty — AI-репетитор английского прямо в Telegram.\n\n"
        "Бесплатно: курс A0-C2, карточки, drills, аудирование, дерево роста.\n"
        "По подписке: живой чат ALEX, roleplay, проверка текста, подготовка к TOEFL · IELTS · CAE. Кредиты ALEX можно докупать отдельно.\n\n"
        "Команды: /start, /premium, /share, /lesson, /vocab, /test, /toefl, /roleplay, /support, /terms, /rules."
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

FOOTER = "\n\n<i>────────────────</i>\n<i>/lesson · /vocab · /test · /toefl · /roleplay · /story · /help</i>"

ICON = {
    "app": "🚀",
    "premium": "💎",
    "lesson": "📘",
    "vocab": "🗂",
    "test": "📝",
    "menu": "📋",
    "support": "💬",
    "share": "🎁",
    "channel": "📣",
    "platform": "🚀",
    "credits": "🪙",
    "audio": "🔊",
    "progress": "📊",
    "success": "✅",
    "warn": "⚠️",
}

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
        return f"{ICON['warn']} Error. Try again.\n<i>{str(e)[:80]}</i>"

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
        mark = "📈" if direction == "up" else "📉"
        msg = (f"\n\n{mark} <b>Уровень изменён на {new_level}</b>" if lang=="ru"
               else f"\n\n{mark} <b>Level adjusted to {new_level}</b>")
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
        return f"{ICON['warn']} <i>{str(e)[:100]}</i>"

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
        f"{ICON['audio']} <b>Генерирую {type_label}...</b>" if lang=="ru" else f"{ICON['audio']} <b>Generating {type_label}...</b>"
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
        await status_msg.edit_text(f"{ICON['warn']} Ошибка. Попробуй ещё раз." if lang=="ru" else f"{ICON['warn']} Error. Try again.")
        return

    await status_msg.edit_text(f"{ICON['audio']} <b>Синтезирую аудио...</b>" if lang=="ru" else f"{ICON['audio']} <b>Generating audio...</b>")

    audio_bytes = await text_to_speech(transcript)

    type_icon = "💬" if content_type == "dialogue" else "📘"
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
                    text="✓ Готов отвечать" if lang=="ru" else "✓ Ready for questions",
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
                    text="✓ Готов" if lang=="ru" else "✓ Ready",
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
        f"{ICON['audio']} <b>{'Результат' if lang=='ru' else 'Result'}</b>\n\n"
        f"✓ {'Правильно' if lang=='ru' else 'Correct'}: <b>{correct}/{len(questions)}</b>\n\n"
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
        InlineKeyboardButton(text="RU · Русский", callback_data="lang_ru"),
        InlineKeyboardButton(text="EN · English", callback_data="lang_en"),
    ]])

def level_kb():
    rows = [[InlineKeyboardButton(text=lv, callback_data=f"setlevel_{lv}")] for lv in LEVEL_ORDER]
    rows.append([InlineKeyboardButton(text="Placement test", callback_data="test_placement")])
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
        "lesson_tenses":       ("⏳ Времена глагола","⏳ Verb Tenses"),
        "lesson_conditionals": ("🔀 Условные","🔀 Conditionals"),
        "lesson_modal":        ("🗝 Модальные глаголы","🗝 Modal Verbs"),
        "lesson_passive":      ("🔄 Пассивный залог","🔄 Passive Voice"),
        "lesson_articles":     ("🔤 Артикли","🔤 Articles"),
        "lesson_prepositions": ("📍 Предлоги","📍 Prepositions"),
        "lesson_phrasal":      ("🧩 Фразовые глаголы","🧩 Phrasal Verbs"),
        "lesson_reported":     ("💬 Косвенная речь","💬 Reported Speech"),
        "lesson_subjunctive":  ("🎯 Сослагательное","🎯 Subjunctive"),
        "lesson_inversion":    ("🔁 Инверсия C1-C2","🔁 Inversion C1-C2"),
    }
    return _simple_kb(items, lang)

def vocab_kb(lang):
    items = {
        "vocab_new":         ("✨ Новые слова","✨ New Words"),
        "vocab_review":      ("🔁 Повторение SM-2","🔁 SM-2 Review"),
        "vocab_flashcards":  ("🃏 Флэш-карточки","🃏 Flashcards"),
        "vocab_collocations":("🔗 Коллокации","🔗 Collocations"),
        "vocab_idioms_adv":  ("💠 Продвинутые идиомы","💠 Advanced Idioms"),
        "vocab_topic":       ("🗂 По теме","🗂 By Topic"),
        "daily_quiz":        ("🎲 Ежедневный квиз","🎲 Daily Quiz"),
        "idioms_cultural":   ("🌍 Культурные идиомы","🌍 Cultural Idioms"),
    }
    return _simple_kb(items, lang)

def test_kb(lang):
    items = {
        "test_grammar":   ("📘 Грамматика","📘 Grammar"),
        "test_vocab":     ("🗂 Лексика","🗂 Vocabulary"),
        "test_reading":   ("📖 Чтение","📖 Reading"),
        "test_writing":   ("✍️ Письмо","✍️ Writing"),
        "test_mixed":     ("🎲 Смешанный","🎲 Mixed"),
        "test_placement": ("🎯 Определить уровень","🎯 Placement Test"),
    }
    return _simple_kb(items, lang)

def toefl_kb(lang):
    items = {
        "toefl_reading":   ("📖 Reading","📖 Reading"),
        "toefl_listening": ("🎧 Listening + Audio","🎧 Listening + Audio"),
        "toefl_speaking1": ("🎙 Speaking Independent","🎙 Speaking Independent"),
        "toefl_speaking2": ("🗣 Speaking Integrated","🗣 Speaking Integrated"),
        "toefl_writing1":  ("✍️ Writing Independent","✍️ Writing Independent"),
        "toefl_writing2":  ("📝 Writing Integrated","📝 Writing Integrated"),
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
        "talk_business":  ("📈 Бизнес English","📈 Business English"),
        "talk_free":      ("💬 Свободная беседа","💬 Free Chat"),
        "talk_interview": ("🤝 Mock Interview","🤝 Mock Interview"),
    }
    return _simple_kb(items, lang)

def remind_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="08:00",callback_data="remind_08:00"),
         InlineKeyboardButton(text="10:00",callback_data="remind_10:00")],
        [InlineKeyboardButton(text="12:00",callback_data="remind_12:00"),
         InlineKeyboardButton(text="18:00",callback_data="remind_18:00")],
        [InlineKeyboardButton(text="19:00",callback_data="remind_19:00"),
         InlineKeyboardButton(text="21:00",callback_data="remind_21:00")],
        [InlineKeyboardButton(text="Disable",callback_data="remind_off")],
    ])

def flashcard_kb(word_id: int, lang: str):
    opts = [("😵 Не знал",1),("😅 Почти",2),("🙂 Помнил",4),("😎 Легко",5)] if lang=="ru" else [("😵 Forgot",1),("😅 Hard",2),("🙂 Good",4),("😎 Easy",5)]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=l,callback_data=f"fc_{word_id}_{q}") for l,q in opts]])

def shadowing_kb(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔁 Ещё раз" if lang=="ru" else "🔁 Hear again", callback_data="shadow_repeat"),
        InlineKeyboardButton(text="✍️ Написать" if lang=="ru" else "✍️ Write it", callback_data="shadow_write"),
    ]])

def pronunciation_kb(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎙 Записать голосовое" if lang=="ru" else "🎙 Record voice", callback_data="pronounce_record"),
        InlineKeyboardButton(text="✎ Написать" if lang=="ru" else "✎ Type instead", callback_data="shadow_write"),
    ]])

# ══════════════════════════════════════════════════════════════════
#  ПЛАНИРОВЩИК
# ══════════════════════════════════════════════════════════════════

# Motivational quotes removed per design decision — the reminder now ships
# only with factual information (streak count, due words). This keeps the
# tone professional and avoids the "child-friendly slogan" feel.
def motivation_line(lang: str = "ru") -> str:
    return ""

async def send_reminder(uid: int):
    user = await get_user(uid)
    if not user: return
    lang      = user.get("lang","ru")
    streak    = await get_streak_count(uid)
    interests = await get_interests(uid)
    due_cnt   = len(await get_due_words(uid, limit=10))
    due_idioms = len(await get_due_idioms(uid, limit=5))

    # Minimal grammar: bold title, then plain inline rows. No decorative
    # glyphs (◌ ▥ ⌖ ⌁) before each line — they read as noise.
    ru_ = lang == "ru"
    interest_hint = ""
    if interests:
        first = interests.split(",")[0].strip()
        interest_hint = f"\n\n{'Сегодня тема' if ru_ else 'Today'}: <b>{html.escape(first, quote=False)}</b>"

    text = ("<b>Время английского</b>" if ru_ else "<b>Time for English</b>") + interest_hint
    rows_extra = []
    if streak > 2:
        rows_extra.append(f"Streak: <b>{streak}</b>")
    if due_cnt:
        rows_extra.append(f"{'Слов на повторение' if ru_ else 'Words to review'}: <b>{due_cnt}</b>  /vocab")
    if due_idioms:
        rows_extra.append(f"{'Идиом на повторение' if ru_ else 'Idioms to review'}: <b>{due_idioms}</b>  /vocab")
    if rows_extra:
        text += "\n\n" + "\n".join(rows_extra)
    # Word of the day now rides along with the daily reminder instead of
    # being a separate 09:00 push, so the user gets one message at the
    # time they picked.
    try:
        w = random.choice(DAILY_WORDS)
        text += (
            f"\n\n<b>{'Слово дня' if ru_ else 'Word of the day'}</b>\n"
            f"<b>{w['word']}</b>  <code>{w['ph']}</code>\n"
            f"{w['tr']}\n"
            f"<i>{w['ex']}</i>"
        )
    except Exception:
        pass
    # Bot-side messages always carry a Channel button so the reader has
    # somewhere to go when they're not ready to open the app.
    app_url = webapp_url()
    rows = []
    if app_url:
        rows.append([InlineKeyboardButton(
            text="Открыть практику" if lang=="ru" else "Open practice",
            web_app=WebAppInfo(url=app_url))])
    rows.append([InlineKeyboardButton(
        text=f"{ICON['channel']} Канал" if lang=="ru" else f"{ICON['channel']} Channel",
        url=channel_url())])
    kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    try: await bot.send_message(uid, text, reply_markup=kb)
    except Exception as e: logger.warning(f"Reminder failed {uid}: {e}")

async def send_weekly_report(uid: int):
    stats = await get_full_stats(uid)
    lang  = await get_lang(uid)
    ru = lang == "ru"
    try:
        app_url = webapp_url()
        rows = []
        if app_url:
            rows.append([InlineKeyboardButton(
                text=f"{ICON['app']} Открыть приложение" if ru else f"{ICON['app']} Open app",
                web_app=WebAppInfo(url=app_url))])
        rows.append([InlineKeyboardButton(
            text=f"{ICON['channel']} Канал" if ru else f"{ICON['channel']} Channel",
            url=channel_url())])
        kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
        await bot.send_message(uid,
            f"<b>{'Отчёт за неделю' if ru else 'Weekly report'}</b>\n\n"
            f"{stats['level']} · {stats['rank']} · <b>{stats['xp']}</b> XP\n"
            f"Streak: <b>{stats['streak']}</b> · Sessions: <b>{stats['sessions']}</b>\n"
            f"Words: <b>{stats['words']}</b> · Tests: <b>{stats['tests']}</b>",
            reply_markup=kb
        )
    except Exception as e:
        logger.warning(f"weekly_report to {uid}: {e}")

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

async def send_scheduled_push(uid: int):
    """Single daily push at the user's chosen reminder time.
    Sunday  -> weekly report (instead of the reminder + word of the day).
    Mon-Sat -> personal reminder with the word of the day attached."""
    is_sunday = False
    try:
        from datetime import datetime
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo("Europe/Moscow"))
        except Exception:
            now = datetime.now()
        is_sunday = now.weekday() == 6  # Mon=0 ... Sun=6
    except Exception:
        is_sunday = False
    if is_sunday:
        await send_weekly_report(uid)
    else:
        await send_reminder(uid)

async def schedule_all():
    # One consolidated daily push per user, fired at the time they chose
    # in reminders. The content depends on the weekday (see
    # send_scheduled_push). Users with reminders disabled get nothing.
    rows = await db("SELECT uid, remind_time FROM users WHERE remind_time IS NOT NULL AND remind_time != 'off'", fetch="all")
    if rows:
        for r in rows:
            try:
                h, m = map(int, r["remind_time"].split(":"))
                scheduler.add_job(send_scheduled_push,"cron",hour=h,minute=m,args=[r["uid"]],id=f"push_{r['uid']}",replace_existing=True)
            except Exception as e:
                logger.warning(f"schedule_all: bad remind_time for uid={r['uid']}: {e}")

# ══════════════════════════════════════════════════════════════════
#  INLINE MODE
# ══════════════════════════════════════════════════════════════════

@dp.inline_query()
async def handle_inline(query: InlineQuery):
    text = query.query.strip()
    qlow = text.lower()

    # "invite" branch — used by the Share buttons in /start and /share.
    # We can't put HTML inline links inside t.me/share/url?text=… (that
    # mechanism only carries plain text and a URL preview). Going through
    # inline mode is the only way to deliver a forwarded message where
    # the call-to-action sits *inside* the text as clickable links.
    if qlow == "" or qlow.startswith("invite") or qlow.startswith("пригласи") or qlow.startswith("share"):
        uid = query.from_user.id
        try:
            lang = await get_lang(uid) or "ru"
        except Exception:
            lang = "ru"
        ru = lang == "ru"
        ref_link = referral_url(uid)
        ch_url = channel_url()
        # Two short bold links, one per line — mirrors the screenshot
        # style ("🎧 Нажми, чтобы найти песню" / "⭐ Telegram звёзды
        # берут здесь"). The whole label is the hyperlink, no raw URL
        # in the text.
        ref_href = html.escape(ref_link, quote=True)
        ch_href = html.escape(ch_url, quote=True)
        msg = (
            f'<b>🎓 PolyGlotty — AI-репетитор английского в Telegram</b>\n\n'
            f'<blockquote>Курс A0–C2, карточки, экзамены и живой чат с ALEX — '
            f'прямо в Telegram, без отдельного приложения.</blockquote>\n'
            f'🚀 <a href="{ref_href}"><b>Открыть PolyGlotty и забрать +50 XP</b></a>\n'
            f'📡 <a href="{ch_href}"><b>Канал с ежедневной практикой</b></a>'
        ) if ru else (
            f'<b>🎓 PolyGlotty — an AI English tutor inside Telegram</b>\n\n'
            f'<blockquote>A0–C2 course, flashcards, exams and live ALEX chat — '
            f'right inside Telegram, no extra app to install.</blockquote>\n'
            f'🚀 <a href="{ref_href}"><b>Open PolyGlotty and grab +50 XP</b></a>\n'
            f'📡 <a href="{ch_href}"><b>Channel with daily practice</b></a>'
        )
        results = [InlineQueryResultArticle(
            id="invite",
            title="Пригласить друга в PolyGlotty" if ru else "Invite a friend to PolyGlotty",
            description=(
                "AI-репетитор английского · бот + канал" if ru
                else "AI English tutor · bot + channel"
            ),
            input_message_content=InputTextMessageContent(
                message_text=msg,
                parse_mode="HTML",
                disable_web_page_preview=False,
            ),
        )]
        await query.answer(results, cache_time=5, is_personal=True)
        return

    if len(text) < 2:
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
            id="1", title=f"{ICON['vocab']} {data.get('translation','')}",
            description=data.get("explanation","")[:100],
            input_message_content=InputTextMessageContent(
                message_text=f"<b>{text}</b> → {data.get('translation','')}\n<i>{data.get('explanation','')}</i>\n{ICON['lesson']} {data.get('example','')}",
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
        BotCommand(command="app", description="Open WebApp / Открыть приложение"),
        BotCommand(command="menu", description="Button menu / Меню кнопок"),
        BotCommand(command="premium", description="Plans and limits / Подписки"),
        BotCommand(command="share", description="Invite friends / Пригласить друзей"),
        BotCommand(command="channel", description="Official channel / Канал"),
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
        BotCommand(command="rules", description="Service rules / Правила сервиса"),
        BotCommand(command="privacy", description="Privacy / Данные"),
    ]
    app_url = webapp_url()
    try:
        await bot.set_my_name(BOT_PROFILE["name_default"])
        await bot.set_my_name(BOT_PROFILE["name_ru"], language_code="ru")
        await bot.set_my_short_description(BOT_PROFILE["short_default"])
        await bot.set_my_short_description(BOT_PROFILE["short_ru"], language_code="ru")
        await bot.set_my_description(BOT_PROFILE["description_default"])
        await bot.set_my_description(BOT_PROFILE["description_ru"], language_code="ru")
        await bot.set_my_commands(commands)
        await bot.set_my_commands(commands, language_code="ru")
        if app_url:
            await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Open App", web_app=WebAppInfo(url=app_url)))
        logger.info("Bot profile metadata updated")
    except Exception as e:
        logger.warning(f"setup_bot_profile failed: {e}")

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid  = message.from_user.id
    name = user_display_name(message.from_user)

    is_new_user = False
    try:
        is_new_user = (await get_user(uid)) is None
        await upsert_user(uid, name)
    except Exception as e:
        logger.error(f"upsert_user error: {e}")

    # New users get a default daily reminder at 19:00 (Europe/Moscow) so
    # they receive the word of the day and the Sunday weekly report out of
    # the box. Existing users who never configured a time are left untouched
    # (their silence is respected — see /remind to opt in or out).
    if is_new_user:
        try:
            await update_user(uid, remind_time="19:00")
            scheduler.add_job(send_scheduled_push, "cron", hour=19, minute=0,
                              args=[uid], id=f"push_{uid}", replace_existing=True)
        except Exception as e:
            logger.warning(f"default reminder setup failed uid={uid}: {e}")

    # Handle deep links
    args = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if args == "premium":
        try:
            await cmd_premium(message)
        except Exception as e:
            logger.error(f"cmd_premium from start error: {e}")
            await message.answer(f"{ICON['warn']} Error loading premium. Try /premium")
        return
    # ── Direct invoice deep links (from WebApp paywall) ──────────────
    if args in PLATFORM_PLANS:
        user_lang = await get_lang(uid) or "ru"
        ok = await _send_platform_invoice(uid, args, user_lang == "ru")
        if not ok:
            await message.answer(f"{ICON['warn']} Не удалось создать платёж. Попробуй /premium" if user_lang == "ru"
                                  else f"{ICON['warn']} Could not create invoice. Try /premium")
        return
    if args in CREDIT_PACKS:
        user_lang = await get_lang(uid) or "ru"
        ok = await _send_credits_invoice(uid, args, user_lang == "ru")
        if not ok:
            await message.answer(f"{ICON['warn']} Не удалось создать платёж. Попробуй /premium" if user_lang == "ru"
                                  else f"{ICON['warn']} Could not create invoice. Try /premium")
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
    app_url = webapp_url()

    kb_buttons = []
    if app_url:
        kb_buttons.append([InlineKeyboardButton(
            text=f"{ICON['app']} Открыть приложение" if ru else f"{ICON['app']} Open App",
            web_app=WebAppInfo(url=app_url)
        )])
    kb_buttons.append([InlineKeyboardButton(
        text=f"{ICON['premium']} Подписки" if ru else f"{ICON['premium']} Plans",
        callback_data="open_premium"
    )])
    kb_buttons.append([
        InlineKeyboardButton(text=f"{ICON['lesson']} Урок" if ru else f"{ICON['lesson']} Lesson", callback_data="quick_lesson"),
        InlineKeyboardButton(text=f"{ICON['vocab']} Слова" if ru else f"{ICON['vocab']} Vocab", callback_data="quick_vocab"),
        InlineKeyboardButton(text=f"{ICON['test']} Тест" if ru else f"{ICON['test']} Test", callback_data="quick_test"),
    ])
    kb_buttons.append([
        InlineKeyboardButton(text=f"{ICON['menu']} Меню" if ru else f"{ICON['menu']} Menu", callback_data="quick_menu"),
        InlineKeyboardButton(text=f"{ICON['support']} Поддержка" if ru else f"{ICON['support']} Support", callback_data="quick_support"),
    ])
    ref_link = referral_url(uid)
    # The Share button uses switch_inline_query so the user picks any
    # chat and the bot answers inline with a rich message containing
    # clickable HTML links inside the text (see handle_inline, "invite"
    # branch). t.me/share/url cannot carry inline HTML links.
    kb_buttons.append([InlineKeyboardButton(
        text=f"{ICON['share']} Пригласить друга" if ru else f"{ICON['share']} Invite a friend",
        switch_inline_query="invite"
    )])
    kb_buttons.append([
        InlineKeyboardButton(
            text=f"{ICON['channel']} Канал" if ru else f"{ICON['channel']} Channel",
            url=channel_url()
        ),
        InlineKeyboardButton(
            text="📜 Правила" if ru else "📜 Rules",
            callback_data="open_terms"
        ),
    ])

    welcome_kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    is_prem = False
    try:
        is_prem = await check_premium(uid)
    except Exception:
        pass
    badge = " ⭐️" if is_prem else ""

    ref_line = "\n\nБонус за приглашение начислен: +50 XP тебе, +150 XP другу." if (ru and ref_applied) else (
        "\n\nReferral bonus applied: +50 XP for you, +150 XP for your friend." if ref_applied else ""
    )
    name_html = html.escape(name, quote=False)
    # Minimal welcome: short pitch, single feature list, channel link.
    # No "tap here" self-link (the Open-App button below handles that),
    # no empty motivation italics, no decorative glyphs.
    channel_link = html_link(channel_url(), "канал с ежедневными словами", bold=True) if ru else html_link(channel_url(), "daily English channel", bold=True)
    text = (
        f"<b>Привет, {name_html}!</b>{badge}\n\n"
        f"<b>PolyGlotty</b> — AI-репетитор английского прямо в Telegram.\n"
        f"Курс, карточки, экзамены и живой чат с ALEX — без отдельного приложения.\n\n"
        f"<blockquote><b>Бесплатно</b>\n"
        f"• Курс A0–C2, карточки, аудирование, drills\n"
        f"• Прогресс, дерево роста, ежедневные цели</blockquote>\n"
        f"<blockquote><b>По подписке</b>\n"
        f"• Живой чат с ALEX, roleplay, проверка текста\n"
        f"• Подготовка к TOEFL · IELTS · CAE\n"
        f"• Кредиты ALEX докупаются отдельно</blockquote>\n"
        f"Жми <b>«Открыть приложение»</b>, чтобы начать. Ежедневная практика — в {channel_link}."
        f"{ref_line}"
    ) if ru else (
        f"<b>Hey, {name_html}!</b>{badge}\n\n"
        f"<b>PolyGlotty</b> is an AI English tutor right inside Telegram.\n"
        f"Course, flashcards, exam prep and live ALEX chat — no extra app to install.\n\n"
        f"<blockquote><b>Free</b>\n"
        f"• A0–C2 course, flashcards, listening, drills\n"
        f"• Progress, growth tree, daily goals</blockquote>\n"
        f"<blockquote><b>With subscription</b>\n"
        f"• Live ALEX chat, roleplay, text check\n"
        f"• TOEFL · IELTS · CAE preparation\n"
        f"• ALEX credits available as top-ups</blockquote>\n"
        f"Tap <b>“Open App”</b> to begin. Daily practice lives in the {channel_link}."
        f"{ref_line}"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=welcome_kb)

@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    lang = await get_lang(message.from_user.id) or "ru"
    await message.answer(
        "Меню кнопок включено. Можно не помнить команды — просто нажимай нужный раздел."
        if lang == "ru" else
        "Button menu is enabled. You do not need to remember commands — just tap a section.",
        reply_markup=main_kb(lang),
    )

@dp.callback_query(F.data.startswith("quick_"))
async def cb_quick_menu(cb: CallbackQuery):
    uid = cb.from_user.id
    lang = await get_lang(uid) or "ru"
    action = cb.data.replace("quick_", "")
    await cb.answer()
    if action == "lesson":
        await cb.message.answer(f"{ICON['lesson']} <b>Grammar Lessons</b>", reply_markup=lesson_kb(lang))
    elif action == "vocab":
        await cb.message.answer(f"{ICON['vocab']} <b>Vocabulary</b>", reply_markup=vocab_kb(lang))
    elif action == "test":
        await cb.message.answer(f"{ICON['test']} <b>Tests</b>", reply_markup=test_kb(lang))
    elif action == "support":
        await send_support_prompt(cb.message, uid)
    else:
        await cb.message.answer(
            "Меню кнопок включено." if lang == "ru" else "Button menu is enabled.",
            reply_markup=main_kb(lang),
        )

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
    link = referral_url(uid)
    link_code = html.escape(link, quote=False)
    text = (
        "<b>🎓 Приглашай друзей в PolyGlotty</b>\n\n"
        "Поделись английским с друзьями — и получай XP за каждого, кто начнёт учиться.\n\n"
        "<blockquote><b>Награда за приглашение</b>\n"
        "• Другу — <b>+50 XP</b> на старт\n"
        "• Тебе — <b>+150 XP</b> за каждого нового ученика</blockquote>\n"
        "<b>Твоя ссылка</b> — нажми, чтобы скопировать:\n"
        f"<code>{link_code}</code>\n\n"
        f"<blockquote>👥 Уже приглашено: <b>{ref_count}</b></blockquote>\n"
        "Жми <b>«Поделиться»</b> — друг получит готовое приглашение со ссылкой внутри."
    ) if ru else (
        "<b>🎓 Invite friends to PolyGlotty</b>\n\n"
        "Share English with your friends — and earn XP for everyone who starts learning.\n\n"
        "<blockquote><b>Referral reward</b>\n"
        "• Your friend — <b>+50 XP</b> to start\n"
        "• You — <b>+150 XP</b> for every new learner</blockquote>\n"
        "<b>Your link</b> — tap to copy:\n"
        f"<code>{link_code}</code>\n\n"
        f"<blockquote>👥 Invited so far: <b>{ref_count}</b></blockquote>\n"
        "Tap <b>“Share”</b> — your friend gets a ready invite with the link inside."
    )
    # The Share button hands off to the inline "invite" flow so that
    # the sent message contains HTML-linked call-to-action labels
    # (PolyGlotty + Channel), not a raw URL preview.
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{ICON['share']} Поделиться" if ru else f"{ICON['share']} Share",
            switch_inline_query="invite"
        )],
        [InlineKeyboardButton(
            text=f"{ICON['channel']} Канал" if ru else f"{ICON['channel']} Channel",
            url=channel_url()
        )],
    ])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.message(Command("channel"))
async def cmd_channel(message: Message):
    lang = await get_lang(message.from_user.id) or "ru"
    ru = lang == "ru"
    link = html_link(channel_url(), "открыть канал PolyGlotty", bold=True) if ru else html_link(channel_url(), "open the PolyGlotty channel", bold=True)
    text = (
        "<b>Канал PolyGlotty</b>\n\n"
        "Короткие посты: слово дня, пример, перевод и ссылка на практику.\n\n"
        f"{link}"
    ) if ru else (
        "<b>PolyGlotty channel</b>\n\n"
        "Short posts: word of the day, example, translation and a practice link.\n\n"
        f"{link}"
    )
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"{ICON['channel']} Канал" if ru else f"{ICON['channel']} Channel", url=channel_url())
        ]])
    )

@dp.callback_query(F.data == "open_premium")
async def cb_open_premium(cb: CallbackQuery):
    await cb.answer()
    await cmd_premium(cb.message)

@dp.callback_query(F.data == "open_terms")
async def cb_open_terms(cb: CallbackQuery):
    await cb.answer()
    lang = await get_lang(cb.from_user.id) or "ru"
    await send_terms(cb.message, lang)

@dp.message(Command("lang"))
async def cmd_lang(m: Message):
    await m.answer("Language / Язык", reply_markup=lang_kb())

@dp.message(Command("level"))
async def cmd_level(m: Message):
    await m.answer("Choose your level / Выбери уровень", reply_markup=level_kb())

@dp.message(Command("lesson"))
async def cmd_lesson(m: Message):
    lang = await get_lang(m.from_user.id)
    await m.answer("<b>Grammar lessons</b>" if lang!="ru" else "<b>Уроки грамматики</b>", reply_markup=lesson_kb(lang))

@dp.message(Command("vocab"))
async def cmd_vocab(m: Message):
    lang = await get_lang(m.from_user.id)
    await m.answer("<b>Vocabulary</b>" if lang!="ru" else "<b>Словарь</b>", reply_markup=vocab_kb(lang))

@dp.message(Command("roleplay"))
async def cmd_roleplay(m: Message):
    lang = await get_lang(m.from_user.id)
    if not await has_access(m.from_user.id, "pro"):
        await send_upgrade_hint(m, "pro", lang, "Roleplay")
        return
    await m.answer("🎭 <b>Roleplay</b>", reply_markup=roleplay_kb(lang))

@dp.message(Command("story"))
async def cmd_story(m: Message):
    lang = await get_lang(m.from_user.id)
    if not await has_access(m.from_user.id, "basic"):
        await send_upgrade_hint(m, "basic", lang, "Story Quest")
        return
    await m.answer("🎮 <b>Story Quest</b>", reply_markup=story_kb(lang))

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
    await m.answer(f"{ICON['test']} <b>Tests</b>", reply_markup=test_kb(lang))

@dp.message(Command("toefl"))
async def cmd_toefl(m: Message):
    lang = await get_lang(m.from_user.id)
    if not await has_access(m.from_user.id, "ultimate"):
        await send_upgrade_hint(m, "ultimate", lang, "TOEFL")
        return
    await m.answer("🎓 <b>TOEFL iBT</b>", reply_markup=toefl_kb(lang))

@dp.message(Command("talk"))
async def cmd_talk(m: Message):
    lang = await get_lang(m.from_user.id)
    await m.answer("💬 <b>Speaking</b>", reply_markup=talk_kb(lang))

@dp.message(Command("tone"))
async def cmd_tone(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    await m.answer(
        (f"{ICON['lesson']} <b>Редактор тона фразы</b>\n\n"
         "Отправь любую фразу на английском. ALEX покажет 5 вариантов с разным стилем:\n"
         "<code>Professional</code> · <code>Polite</code> · <code>Assertive</code> · <code>Soft</code> · <code>Casual</code>")
        if lang=="ru" else
        (f"{ICON['lesson']} <b>Tone Editor</b>\n\n"
         "Send any English phrase. ALEX will show 5 versions with different tones:\n"
         "<code>Professional</code> · <code>Polite</code> · <code>Assertive</code> · <code>Soft</code> · <code>Casual</code>")
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
            caption=f"{ICON['audio']} <b>Shadowing</b>\n\n<i>{phrase}</i>\n\n{'Запиши голосовое или напиши:' if lang=='ru' else 'Record a voice message or type:'}",
            reply_markup=pronunciation_kb(lang)
        )
    else:
        await m.answer(f"{ICON['audio']} <b>Shadowing</b>\n\n<i>{phrase}</i>", reply_markup=shadowing_kb(lang))
    waiting[uid] = "shadowing"
    log_session(uid, "shadowing")

@dp.message(Command("writing"))
async def cmd_writing(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    await m.answer(
        (f"{ICON['lesson']} <b>Проверка текста</b>\n\n"
         "Отправь текст. ALEX вернёт:\n"
         "<code>Corrected</code> · <code>Native-like</code> · <code>Error breakdown</code>")
        if lang=="ru" else
        (f"{ICON['lesson']} <b>Writing Check</b>\n\n"
         "Send a text. ALEX will return:\n"
         "<code>Corrected</code> · <code>Native-like</code> · <code>Error breakdown</code>")
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
        (f"{ICON['lesson']} <b>О себе</b>\n\n"
         "Напиши профессию, интересы или цель обучения.\n"
         "Например: <code>Java Developer, хочу говорить на митингах</code>\n\n"
         "ALEX будет подбирать примеры ближе к твоему контексту.")
        if lang=="ru" else
        (f"{ICON['lesson']} <b>About You</b>\n\n"
         "Write your field, interests, or learning goal.\n"
         "Example: <code>Java Developer, I want to speak in meetings</code>\n\n"
         "ALEX will make examples closer to your context.")
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
    # Clean stats: plain typographic hierarchy, no decorative glyphs,
    # no ASCII progress bar. Profession and interests appear as their
    # own labelled rows, not as captioned icons.
    ru_ = lang == "ru"
    body = (
        f"<b>{'Прогресс' if ru_ else 'Progress'}</b>\n\n"
        f"{stats['level']} · {stats['rank']}\n"
        f"XP: <b>{xp}</b> / {nxt}\n\n"
        f"Streak: <b>{stats['streak']}</b> · Sessions: <b>{stats['sessions']}</b>\n"
        f"Words: <b>{stats['words']}</b> · Tests: <b>{stats['tests']}</b>\n"
        f"Errors: <b>{stats['errors']}</b> · TOEFL: <b>{stats['toefl']}</b>"
    )
    if profession:
        body += f"\n\n{'Профессия' if ru_ else 'Profession'}: <i>{html.escape(str(profession), quote=False)}</i>"
    body += f"\n{'Интересы' if ru_ else 'Interests'}: <i>{html.escape(interest_line, quote=False)}</i>"
    await m.answer(body)

@dp.message(Command("mistakes"))
async def cmd_mistakes(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    rows = await get_mistakes(uid, limit=10)
    ru = lang == "ru"
    if not rows:
        await m.answer(
            "Пока пусто.\n\nПиши ALEX на английском или проверь текст в приложении — исправления появятся здесь."
            if ru else
            "Nothing here yet.\n\nWrite to ALEX in English or check a text in the app — corrections will appear here."
        )
        return
    text = (
        "<b>Дневник ошибок ALEX</b>\n"
        "Последние исправления из чата и проверки текста.\n\n"
        if ru else
        "<b>ALEX Error Diary</b>\n"
        "Recent corrections from chat and text checks.\n\n"
    )
    for i, r in enumerate(rows, 1):
        original = html.escape(str(r["original"])[:90], quote=False)
        corrected = html.escape(str(r["corrected"])[:90], quote=False)
        explanation = html.escape(str(r["explanation"] or "")[:120], quote=False)
        text += (
            f"<b>{i}.</b> <s>{original}</s>\n"
            f"<b>→</b> <code>{corrected}</code>\n"
            f"<i>{explanation}</i>\n\n"
        )
    text += "Открой WebApp, чтобы попросить ALEX разобрать повторяющиеся паттерны." if ru else "Open the WebApp to ask ALEX to review repeated patterns."
    await m.answer(text.rstrip())

@dp.message(Command("interests"))
async def cmd_interests(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
    rows = await get_all_interests(uid)
    current = ", ".join(r["interest"] for r in rows) if rows else ("пусто" if lang=="ru" else "empty")
    await m.answer(
        f"<b>{'Интересы' if lang=='ru' else 'Interests'}</b>\n\n"
        f"{'Сейчас' if lang=='ru' else 'Now'}: <i>{html.escape(current, quote=False)}</i>\n\n"
        f"{'Напиши через запятую — ALEX также запоминает их из разговора.' if lang=='ru' else 'Write comma-separated — ALEX also picks them up from chat.'}\n"
        f"<code>gaming, music, travel, tech</code>"
    )
    waiting[uid] = "set_interests"

@dp.message(Command("remind"))
async def cmd_remind(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    text = (
        "<b>Ежедневное напоминание</b>\n\n"
        "Выбери время, когда ALEX будет возвращать тебя к английскому.\n"
        "В это же время придёт слово дня, а по воскресеньям — отчёт за неделю."
    ) if lang == "ru" else (
        "<b>Daily reminder</b>\n\n"
        "Choose when ALEX should bring you back to English practice.\n"
        "The word of the day arrives at the same time, and on Sundays — your weekly report."
    )
    await m.answer(text, reply_markup=remind_kb())

@dp.message(Command("help"))
async def cmd_help(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    app_url = webapp_url()
    kb = None
    if app_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{ICON['app']} Открыть приложение" if lang=="ru" else f"{ICON['app']} Open App",
                                  web_app=WebAppInfo(url=app_url))],
            [InlineKeyboardButton(text=f"{ICON['premium']} Подписки" if lang=="ru" else f"{ICON['premium']} Plans", callback_data="open_premium")],
            [InlineKeyboardButton(text=f"{ICON['share']} Пригласить друга" if lang=="ru" else f"{ICON['share']} Invite a friend", switch_inline_query="invite")],
            [
                InlineKeyboardButton(text=f"{ICON['channel']} Канал" if lang=="ru" else f"{ICON['channel']} Channel", url=channel_url()),
                InlineKeyboardButton(text="📜 Правила" if lang=="ru" else "📜 Rules", callback_data="open_terms"),
            ],
        ])
    await m.answer(
        ("<b>PolyGlotty · AI-репетитор английского</b>\n\n"
         "<blockquote><b>В приложении</b>\n"
         "• Бесплатный курс A0–C2\n"
         "• Карточки и повторение (SRS)\n"
         "• Grammar games · Story Mode\n"
         "• Подготовка к TOEFL · IELTS · CAE\n"
         "• Чат с ALEX по подписке</blockquote>\n"
         "<blockquote expandable><b>Команды</b>\n"
         "/app — приложение\n"
         "/menu — кнопки без команд\n"
         "/premium — подписки и кредиты\n"
         "/share — пригласить друга\n"
         "/channel — канал\n"
         "/support — поддержка\n"
         "/terms · /rules · /privacy — правовая часть</blockquote>") if lang=="ru" else
        ("<b>PolyGlotty · AI English tutor</b>\n\n"
         "<blockquote><b>Inside the app</b>\n"
         "• Free A0–C2 course\n"
         "• Flashcards and review (SRS)\n"
         "• Grammar games · Story Mode\n"
         "• TOEFL · IELTS · CAE prep\n"
         "• ALEX chat on subscription</blockquote>\n"
         "<blockquote expandable><b>Commands</b>\n"
         "/app — open the app\n"
         "/menu — buttons without commands\n"
         "/premium — subscriptions & credits\n"
         "/share — invite a friend\n"
         "/channel — channel\n"
         "/support — support\n"
         "/terms · /rules · /privacy — legal</blockquote>"),
        reply_markup=kb
    )

@dp.message(Command("support"))
async def cmd_support(m: Message):
    await send_support_prompt(m, m.from_user.id)

async def send_support_prompt(target, uid: int):
    lang = await get_lang(uid) or "ru"
    ru = lang == "ru"
    waiting[uid] = "support_message"
    kb_rows = [[InlineKeyboardButton(text=f"{ICON['support']} Написать в поддержку" if ru else f"{ICON['support']} Message support", url=support_contact_url())]]
    if RAILWAY_URL and "localhost" not in RAILWAY_URL:
        kb_rows.append([InlineKeyboardButton(text=f"{ICON['app']} Открыть приложение" if ru else f"{ICON['app']} Open App",
                                             web_app=WebAppInfo(url=webapp_url()))])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await target.answer(
        ("<b>Поддержка PolyGlotty</b>\n\n"
         "Опиши проблему одним сообщением, и мы передадим её в поддержку:\n"
         "• что произошло;\n"
         "• есть ли у тебя подписка / кредиты;\n"
         "• примерное время ошибки;\n"
         "• что ты нажимал перед ошибкой.\n\n"
         "Можно также написать напрямую по кнопке ниже.")
        if ru else
        ("<b>PolyGlotty Support</b>\n\n"
         "Describe the issue in one message and we will send it to support:\n"
         "• what happened;\n"
         "• whether you have a subscription / credits;\n"
         "• approximate error time;\n"
         "• what you tapped before the issue.\n\n"
         "You can also message directly using the button below."),
        reply_markup=kb
    )

@dp.message(Command("paysupport"))
async def cmd_paysupport(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    ru = lang == "ru"
    waiting[m.from_user.id] = "support_message"
    await m.answer(
        ("<b>Поддержка оплаты</b>\n\n"
         "Подписка и кредиты ALEX оплачиваются через Telegram Stars. После успешной оплаты доступ обычно появляется сразу.\n\n"
         "Если доступ не появился:\n"
         "1. Перезапусти мини-приложение.\n"
         "2. Нажми /premium и проверь статус.\n"
         "3. Напиши сюда: что покупал, время оплаты и скрин платежа.\n\n"
         "Возвраты и спорные платежи обрабатываются по правилам Telegram Stars.")
        if ru else
        ("<b>Payment Support</b>\n\n"
         "The subscription and ALEX credits are paid through Telegram Stars. After a successful payment, access normally appears immediately.\n\n"
         "If access did not appear:\n"
         "1. Restart the mini app.\n"
         "2. Tap /premium and check your status.\n"
         "3. Send what you bought, payment time and payment screenshot here.\n\n"
         "Refunds and disputed payments follow Telegram Stars rules.")
    )

@dp.message(Command("terms"))
@dp.message(Command("rules"))
async def cmd_terms(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    await send_terms(m, lang)

async def send_terms(target, lang: str):
    ru = lang == "ru"
    text = (
        "<b>📜 Условия и правила PolyGlotty</b>\n\n"
        "<blockquote expandable>"
        "1. <b>О сервисе.</b> PolyGlotty — образовательное Telegram-приложение для изучения английского. Это не официальная школа, экзаменационный центр, юридическая, медицинская или финансовая консультация и не государственное учреждение.\n\n"
        "2. <b>Без гарантии результата.</b> PolyGlotty помогает заниматься регулярно, но не гарантирует поступление, трудоустройство, баллы за экзамен, свободную речь за фиксированный срок или одинаковую скорость прогресса для всех.\n\n"
        "3. <b>ИИ может ошибаться.</b> ALEX объясняет, проверяет тексты и помогает практиковаться, но ИИ может ошибаться, терять контекст или давать неточные формулировки. Важную информацию стоит перепроверять. Ответы ИИ — не профессиональная консультация.\n\n"
        "4. <b>Оплата.</b> Подписки оплачиваются через Telegram Stars и привязаны к твоему Telegram ID. Если оплата прошла, а доступ не появился — напиши в /paysupport (план, время оплаты, скриншот).\n\n"
        "5. <b>Вне нашего контроля.</b> PolyGlotty не управляет Telegram, Telegram WebView, Telegram Stars, App Store, Google Play, банками, Anthropic, моделями ИИ, TTS/STT-провайдерами, Railway, интернет-соединением, устройствами, ОС и региональными ограничениями.\n\n"
        "6. <b>Лимиты и устойчивость.</b> Лимиты, модели, стоимость в кредитах, награды XP, цены, состав планов и доступ к функциям могут меняться, чтобы сервис оставался устойчивым и защищённым от злоупотреблений.\n\n"
        "7. <b>Честное использование.</b> Запрещено обходить лимиты, создавать фейковые аккаунты, автоматизировать запросы, скрапить контент, злоупотреблять платежами, атаковать API, рассылать спам и выдавать внутренние сертификаты прогресса за официальные документы.\n\n"
        "8. <b>Данные и безопасность.</b> Не отправляй пароли, банковские данные, документы, личную переписку, медицинские записи, адреса и другие чувствительные данные. Подробнее: /privacy.\n\n"
        "9. <b>Поддержка.</b> Обращения обрабатываются в очереди, ответ может быть не мгновенным. Вопросы по приложению: /support. По оплате: /paysupport."
        "</blockquote>\n"
        "<i>Продолжая пользоваться ботом или оформляя подписку, ты соглашаешься с этими правилами.</i>"
    ) if ru else (
        "<b>📜 PolyGlotty Terms and Rules</b>\n\n"
        "<blockquote expandable>"
        "1. <b>Service scope.</b> PolyGlotty is an educational Telegram WebApp for English learning. It is not an official school, exam center, legal advisor, medical service, financial advisor or government institution.\n\n"
        "2. <b>No guaranteed results.</b> PolyGlotty helps users study consistently, but does not guarantee admission, employment, exam scores, fluent speaking within a fixed time, or the same progress speed for every user.\n\n"
        "3. <b>AI can be wrong.</b> ALEX can explain, check texts and help users practise, but AI may make mistakes, miss context or give inaccurate wording. Important information should be verified. AI replies are not professional advice.\n\n"
        "4. <b>Payments.</b> Digital subscriptions are paid through Telegram Stars and linked to the user's Telegram ID. If payment succeeded but access did not appear, contact /paysupport with the plan, payment time and screenshot.\n\n"
        "5. <b>Outside our control.</b> PolyGlotty does not control Telegram, Telegram WebView, Telegram Stars, App Store, Google Play, banks, Anthropic, AI models, TTS/STT providers, Railway, internet connection, devices, operating systems or regional restrictions.\n\n"
        "6. <b>Limits and sustainability.</b> Limits, models, quota costs, XP rewards, prices, plan contents and feature access may change to keep the service sustainable, prevent abuse and adapt to provider pricing.\n\n"
        "7. <b>Fair use.</b> Users must not bypass limits, create fake accounts, automate requests, scrape content, abuse payments, attack APIs, send spam, or present internal progress certificates as official accredited documents.\n\n"
        "8. <b>Data and safety.</b> Do not send passwords, bank details, documents, private conversations, medical records, addresses or other sensitive data. Details: /privacy.\n\n"
        "9. <b>Support.</b> Requests are handled in queue, so replies may not be instant. App questions: /support. Payment questions: /paysupport."
        "</blockquote>\n"
        "<i>By continuing to use the bot or buying a subscription, you agree to these rules.</i>"
    )
    await target.answer(text)

@dp.message(Command("privacy"))
async def cmd_privacy(m: Message):
    lang = await get_lang(m.from_user.id) or "ru"
    ru = lang == "ru"
    await m.answer(
        ("<b>Данные и приватность</b>\n\n"
         "Мы сохраняем данные, которые нужны для обучения: Telegram ID, имя/ник, язык, уровень, XP, прогресс, подписку, карточки, ошибки и настройки обучения.\n"
         "Чат с ALEX используется для ответа и улучшения персонального контекста внутри продукта. Сообщения и голосовые запросы могут обрабатываться AI/TTS/STT-провайдерами только для работы функций.\n"
         "Мы не продаём персональные данные.\n\n"
         "Чтобы запросить удаление данных, напиши /support и укажи свой Telegram ID. Удаление данных может отключить прогресс, подписочные настройки и персонализацию.")
        if ru else
        ("<b>Data and Privacy</b>\n\n"
         "We store data required for learning: Telegram ID, name/username, language, level, XP, progress, subscription, flashcards, mistakes and learning settings.\n"
         "ALEX chat is used to answer and improve personal context inside the product. Messages and voice requests may be processed by AI/TTS/STT providers only to run the features.\n"
         "We do not sell personal data.\n\n"
         "To request deletion, contact /support and include your Telegram ID. Deleting data may disable progress, subscription settings and personalization.")
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
    await cb.answer("✓")
    name = html.escape(user_display_name(cb.from_user), quote=False)
    await cb.message.answer(f"<b>{'Привет' if lang=='ru' else 'Hey'}, {name}!</b>\n\nPolyGlotty готов к практике.", reply_markup=main_kb(lang))
    await cb.message.answer("🎯 <b>Level</b>", reply_markup=level_kb())

@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = await get_lang(uid)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(f"{ICON['menu']} Menu", reply_markup=main_kb(lang))
    await cb.answer()

@dp.callback_query(F.data.startswith("setlevel_"))
async def cb_setlevel(cb: CallbackQuery):
    uid   = cb.from_user.id
    lang  = await get_lang(uid)
    level = cb.data.replace("setlevel_","")
    await update_user(uid, level=level)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(f"✓ {level}")
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, f"My English level is {level}. Give a brief encouraging welcome, what to focus on, and suggest what to start with today.", mode="general")
    await cb.message.answer(reply)

@dp.callback_query(F.data.startswith("rp_"))
async def cb_roleplay(cb: CallbackQuery):
    uid      = cb.from_user.id
    lang     = await get_lang(uid)
    if not await has_access(uid, "pro"):
        await cb.answer("Нужна подписка" if lang == "ru" else "Subscription required", show_alert=True)
        await send_upgrade_hint(cb.message, "pro", lang, "Roleplay")
        return
    scenario = ROLEPLAY_SCENARIOS.get(cb.data)
    if not scenario: await cb.answer(); return
    if cb.data == "rp_custom":
        await cb.answer()
        await cb.message.answer(f"{ICON['lesson']} Опиши ситуацию:" if lang=="ru" else f"{ICON['lesson']} Describe the scenario:")
        waiting[uid] = "rp_custom"
        return
    if cb.data == "rp_smart":
        # Динамический ролплей по профессии
        await cb.answer()
        profession = await get_profession(uid)
        if not profession:
            await cb.message.answer(f"{ICON['lesson']} " + ("Сначала заполни /profession" if lang=="ru" else "First fill in /profession"))
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
            await cb.message.answer("✓ No words due today." if lang=="en" else "✓ Нет слов для повторения.")
            return
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer()
        word = due[0]
        set_ctx(uid, review_queue=due, review_idx=0)
        await cb.message.answer(
            f"🃏 <b>Card 1/{len(due)}</b>\n\n<b>{word['word']}</b>\n\n<i>{word['example']}</i>\n\n"
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
        await cb.message.answer(f"✓ <b>{word_row['word']}</b> = {word_row['translation']}\n<i>{word_row['example']}</i>")
    if idx < len(queue):
        word = queue[idx]
        await cb.message.answer(
            f"🃏 <b>Card {idx+1}/{len(queue)}</b>\n\n<b>{word['word']}</b>\n\n<i>{word['example']}</i>",
            reply_markup=flashcard_kb(word["id"], lang)
        )
    else:
        await cb.message.answer(f"✓ Done. +{len(queue)*3} XP")
        log_session(uid, "vocab_review")
        clear_ctx(uid)
    await cb.answer()

# TOEFL — специфичные хэндлеры ПЕРВЫЕ
@dp.callback_query(F.data == "toefl_q_start")
async def cb_toefl_q_start(cb: CallbackQuery):
    uid  = cb.from_user.id
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✓")
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
    await cb.answer(f"✓ {answer}")
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
        if not rows: await cb.message.answer("No scores yet."); return
        text = f"{ICON['test']} <b>TOEFL Scores</b>\n\n"
        for r in rows: text += f"• <b>{r['section']}</b>: best {r['best']}, avg {r['avg_s']:.0f} ({r['cnt']} sessions)\n"
        await cb.message.answer(text); return
    if not await has_access(uid, "ultimate"):
        await cb.answer("Нужна подписка" if lang == "ru" else "Subscription required", show_alert=True)
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
            if audio: await cb.message.answer_voice(BufferedInputFile(audio,"phrase.mp3"), caption=f"{ICON['audio']} <i>{phrase}</i>")
            else: await cb.message.answer(f"{ICON['audio']} <i>{phrase}</i>")
    elif cb.data in ("shadow_write","pronounce_record"):
        await cb.message.answer(f"{ICON['lesson']} Write the phrase:" if lang=="en" else f"{ICON['lesson']} Напиши или запиши голосовое:")
        waiting[uid] = "shadowing"
    await cb.answer()

@dp.callback_query(F.data.startswith("remind_"))
async def cb_remind(cb: CallbackQuery):
    uid  = cb.from_user.id
    data = cb.data.replace("remind_","")
    if data == "off":
        await update_user(uid, remind_time="off")
        for jid in (f"push_{uid}", f"remind_{uid}", f"weekly_{uid}", f"daily_{uid}"):
            if scheduler.get_job(jid): scheduler.remove_job(jid)
        lang = await get_lang(uid) or "ru"
        await cb.answer(); await cb.message.edit_text("Reminders disabled." if lang=="en" else "Напоминания выключены.")
    else:
        lang = await get_lang(uid) or "ru"
        await update_user(uid, remind_time=data)
        try:
            h, m = map(int, data.split(":"))
            scheduler.add_job(send_scheduled_push,"cron",hour=h,minute=m,args=[uid],id=f"push_{uid}",replace_existing=True)
        except Exception as e: logger.warning(e)
        await cb.answer(f"✓ {data}")
        await cb.message.edit_text(
            f"<b>Reminder set</b>\n{data}" if lang=="en"
            else f"<b>Напоминание установлено</b>\n{data}"
        )

# ══════════════════════════════════════════════════════════════════
#  ФОТО — Vision Learning
# ══════════════════════════════════════════════════════════════════

@dp.message(F.photo)
async def handle_photo(message: Message):
    lang = await get_lang(message.from_user.id) or "ru"
    await message.answer(
        f"{ICON['app']} Фото-анализ доступен в приложении. Нажми App внизу." if lang=="ru"
        else f"{ICON['app']} Photo analysis is available in the app. Tap App below."
    )

@dp.message(F.voice)
async def handle_voice(message: Message):
    lang = await get_lang(message.from_user.id) or "ru"
    await message.answer(
        f"{ICON['audio']} Голосовые сообщения обрабатываются в приложении. Нажми App внизу." if lang=="ru"
        else f"{ICON['audio']} Voice messages are processed in the app. Tap App below."
    )

# ══════════════════════════════════════════════════════════════════
#  ТЕКСТ
# ══════════════════════════════════════════════════════════════════

MENU_RU = {
    "▤ Урок грамматики":"lesson","◌ Словарь":"vocab","◇ Ролевой диалог":"roleplay",
    "▧ Story Quest":"story","✓ Тест":"test","◍ TOEFL":"toefl",
    "✎ Проверить текст":"writing","⌁ Тон фразы":"tone",
    "◦ Разговор":"talk","△ Дебаты":"debate","◆ Идиомы":"idioms","! Мои ошибки":"mistakes","▥ Прогресс":"stats",
    # Legacy labels kept so older Telegram keyboards still route correctly.
    "📚 Урок грамматики":"lesson","📝 Словарь":"vocab","🎭 Ролевой диалог":"roleplay",
    "🎮 Story Quest":"story","✅ Тест":"test","🎓 TOEFL":"toefl",
    "✍️ Проверить текст":"writing","🎨 Тон фразы":"tone","💬 Разговор":"talk",
    "⚔️ Дебаты":"debate","🗣 Идиомы":"idioms","❌ Мои ошибки":"mistakes","📊 Прогресс":"stats",
}
MENU_EN = {
    "▤ Grammar Lesson":"lesson","◌ Vocabulary":"vocab","◇ Roleplay":"roleplay",
    "▧ Story Quest":"story","✓ Test":"test","◍ TOEFL":"toefl",
    "✎ Check Writing":"writing","⌁ Tone Editor":"tone",
    "◦ Speaking":"talk","△ Debate":"debate","◆ Idioms":"idioms","! My Mistakes":"mistakes","▥ Progress":"stats",
    "📚 Grammar Lesson":"lesson","📝 Vocabulary":"vocab","🎭 Roleplay":"roleplay",
    "🎮 Story Quest":"story","✅ Test":"test","🎓 TOEFL":"toefl",
    "✍️ Check Writing":"writing","🎨 Tone Editor":"tone","💬 Speaking":"talk",
    "⚔️ Debate":"debate","🗣 Idioms":"idioms","❌ My Mistakes":"mistakes","📊 Progress":"stats",
}

@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    """Lightweight handler - redirect to WebApp, no AI calls."""
    uid   = message.from_user.id
    lang  = await get_lang(uid) or "ru"
    text  = message.text.strip()
    state = waiting.get(uid,"")

    # Handle pending states (no AI, just data saving)
    if state == "support_message":
        waiting.pop(uid, None)
        user = message.from_user
        contact = user_display_name(user)
        safe_text = html.escape(text[:3500])
        safe_contact = html.escape(contact)
        try:
            await bot.send_message(
                SUPPORT_USER_ID,
                "<b>PolyGlotty support request</b>\n\n"
                f"User: {safe_contact}\n"
                f"UID: <code>{uid}</code>\n"
                f"Lang: {lang}\n\n"
                f"{safe_text}",
                parse_mode="HTML",
            )
            await message.answer(
                "Сообщение отправлено в поддержку PolyGlotty. Если вопрос срочный, можно ещё написать напрямую через /support."
                if lang == "ru" else
                "Your message was sent to PolyGlotty Support. If it is urgent, you can also message directly through /support."
            )
        except Exception as e:
            logger.error("support forward failed uid=%s: %s", uid, e)
            await message.answer(
                "Не смог переслать сообщение автоматически. Нажми /support и напиши напрямую по кнопке."
                if lang == "ru" else
                "I could not forward it automatically. Tap /support and message directly with the button."
            )
        return

    if state == "set_interests":
        waiting.pop(uid, None)
        for interest in [i.strip() for i in text.split(",") if i.strip()]:
            await save_interest(uid, interest, source="manual")
        await update_user(uid, interests=text[:200])
        await message.answer(f"✓ {'Сохранено' if lang=='ru' else 'Saved'}\n<i>{html.escape(text[:200])}</i>")
        return

    if state == "set_profession":
        waiting.pop(uid, None)
        await update_user(uid, profession=text[:100])
        await save_interest(uid, text[:50], source="profession")
        await message.answer(f"{ICON['lesson']} {'Сохранено:' if lang=='ru' else 'Saved:'} <b>{html.escape(text[:100])}</b>")
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
    app_url = webapp_url()
    kb = None
    if app_url:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"{ICON['app']} Открыть приложение" if lang=="ru" else f"{ICON['app']} Open App",
                                  web_app=WebAppInfo(url=app_url))],
            [InlineKeyboardButton(text=f"{ICON['premium']} Подписки" if lang=="ru" else f"{ICON['premium']} Plans", callback_data="open_premium")],
            [InlineKeyboardButton(text=f"{ICON['channel']} Канал" if lang=="ru" else f"{ICON['channel']} Channel", url=channel_url())],
        ])
    await message.answer(
        ("<b>Продолжи в приложении</b>\n\n"
         "Free даёт карточки, drills, игры и прогресс. ALEX Chat открывается по подписке.") if lang=="ru" else
        ("<b>Continue in the app</b>\n\n"
         "Free includes flashcards, drills, games and progress. ALEX Chat starts with a subscription."),
        reply_markup=kb
    )

@dp.message(Command("app"))
async def cmd_app(m: Message):
    """Открывает WebApp."""
    uid  = m.from_user.id
    lang = await get_lang(uid)
    domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    if not domain:
        await m.answer("! WebApp URL not configured." if lang=="en" else "! WebApp ещё не настроен.")
        return
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
    await m.answer(
        f"{ICON['app']} <b>PolyGlotty App</b>\n\n"
        + ("Открывай приложение — там твой прогресс, флэш-карточки и статистика!" if lang=="ru"
           else "Open the app — your progress, flashcards and stats!"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"{ICON['app']} Открыть приложение" if lang=="ru" else f"{ICON['app']} Open App",
                web_app=WebAppInfo(url=webapp_url() or f"https://{domain}")
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
        await message.answer("✓ Language updated." if new_lang=="en" else "✓ Язык обновлён.")
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
            f"{ICON['vocab']} Напиши слово, которое хочешь добавить:" if lang=="ru"
            else f"{ICON['vocab']} Write the word you want to add:"
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
    # One subscription model now: no tier-specific copy. Free covers
    # the course + cards + drills; the subscription unlocks chat and
    # advanced features. ALEX credits are a separate top-up.
    text = (
        f"<b>{feature}</b> — по подписке.\n\n"
        "В бесплатной версии остаются курс, карточки, drills и прогресс. "
        "Подписка открывает живой чат с ALEX, roleplay, проверку текста и подготовку к экзаменам."
        if lang == "ru" else
        f"<b>{feature}</b> requires a subscription.\n\n"
        "Free still includes the course, flashcards, drills and progress. "
        "The subscription unlocks live ALEX chat, roleplay, text check and exam prep."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=("Подписка и кредиты" if lang == "ru" else "Subscription and credits"),
        callback_data="open_premium"
    )]])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

async def grant_premium_via_server(uid: int, months: int, tier: str = "ultimate") -> bool:
    """Grant premium after payment. Falls back to direct DB write if server API is unavailable."""
    try:
        local_port = os.getenv("PORT", "8080")
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"http://127.0.0.1:{local_port}/api/premium/grant",
                json={"uid": uid, "months": months, "tier": tier, "secret": BOT_SECRET}
            )
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if data.get("ok") is True:
                    return True
            logger.error("grant_premium_via_server failed uid=%s status=%s body=%s", uid, r.status_code, r.text[:500])
    except Exception as e:
        logger.error(f"grant_premium_via_server error: {e}")
    try:
        from database import set_premium
        await set_premium(uid, months, tier)
        logger.info("premium granted directly uid=%s tier=%s months=%s", uid, tier, months)
        return True
    except Exception as e:
        logger.error("direct premium grant failed uid=%s tier=%s months=%s error=%s", uid, tier, months, e)
        return False


async def grant_platform_via_server(uid: int, period: str) -> bool:
    """Grant Platform subscription via server API, fallback to direct DB write."""
    try:
        local_port = os.getenv("PORT", "8080")
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"http://127.0.0.1:{local_port}/api/platform/grant",
                json={"uid": uid, "period": period, "secret": BOT_SECRET}
            )
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if data.get("ok") is True:
                    return True
            logger.error("grant_platform_via_server failed uid=%s status=%s body=%s", uid, r.status_code, r.text[:500])
    except Exception as e:
        logger.error(f"grant_platform_via_server error: {e}")
    try:
        from database import grant_platform
        await grant_platform(uid, period)
        logger.info("platform granted directly uid=%s period=%s", uid, period)
        return True
    except Exception as e:
        logger.error("direct platform grant failed uid=%s period=%s error=%s", uid, period, e)
        return False


async def grant_credits_via_server(uid: int, credits: int) -> bool:
    """Top up ALEX credits via server API, fallback to direct DB write."""
    try:
        local_port = os.getenv("PORT", "8080")
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"http://127.0.0.1:{local_port}/api/credits/grant",
                json={"uid": uid, "credits": credits, "secret": BOT_SECRET}
            )
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    data = {}
                if data.get("ok") is True:
                    return True
            logger.error("grant_credits_via_server failed uid=%s status=%s body=%s", uid, r.status_code, r.text[:500])
    except Exception as e:
        logger.error(f"grant_credits_via_server error: {e}")
    try:
        from database import add_credits
        await add_credits(uid, credits)
        logger.info("credits granted directly uid=%s credits=%s", uid, credits)
        return True
    except Exception as e:
        logger.error("direct credits grant failed uid=%s credits=%s error=%s", uid, credits, e)
        return False

# ══ /premium COMMAND ══════════════════════════════════════════════════════════
@dp.message(Command("premium"))
async def cmd_premium(msg: Message):
    uid = msg.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"

    grandfathered = ""
    platform_info = {}
    credits_balance = 0
    try:
        from database import get_premium_info, grandfather_legacy_tier, get_platform_info, get_credits
        try:
            grandfathered = await grandfather_legacy_tier(uid)
        except Exception:
            grandfathered = ""
        platform_info = await get_platform_info(uid)
        credits_balance = await get_credits(uid)
    except Exception:
        pass

    # ── SINGLE MODULAR MENU (everyone) ───────────────────────────────
    # There is only one subscription now: Platform + ALEX credits.
    # The legacy Basic/Pro/Ultimate bundle is no longer sold to anyone —
    # grandfathered users keep their remaining access but renew via the
    # Platform plan like everybody else.
    if True:
        plat_kb = [
            [InlineKeyboardButton(
                text=("🚀 Платформа · 1 мес — 299 ⭐ + 30 кр" if ru else "🚀 Platform · 1 mo — 299 ⭐ + 30 cr"),
                callback_data="plat_buy:plat_1m"
            )],
            [InlineKeyboardButton(
                text=("🚀 Платформа · 6 мес — 1 290 ⭐ + 150 кр (−28%)" if ru else "🚀 Platform · 6 mo — 1 290 ⭐ + 150 cr (−28%)"),
                callback_data="plat_buy:plat_6m"
            )],
            [InlineKeyboardButton(
                text=("🚀 Платформа · навсегда — 4 990 ⭐ + 500 кр" if ru else "🚀 Platform · lifetime — 4 990 ⭐ + 500 cr"),
                callback_data="plat_buy:plat_life"
            )],
            [InlineKeyboardButton(
                text=("🪙 100 кредитов ALEX — 149 ⭐" if ru else "🪙 100 ALEX credits — 149 ⭐"),
                callback_data="credit_buy:credits_100"
            )],
            [InlineKeyboardButton(
                text=("🪙 500 кредитов ALEX — 599 ⭐ (−20%)" if ru else "🪙 500 ALEX credits — 599 ⭐ (−20%)"),
                callback_data="credit_buy:credits_500"
            )],
            [InlineKeyboardButton(
                text=("🪙 2 000 кредитов ALEX — 1 990 ⭐ (−33%)" if ru else "🪙 2 000 ALEX credits — 1 990 ⭐ (−33%)"),
                callback_data="credit_buy:credits_2000"
            )],
        ]
        status_lines = []
        if platform_info.get("active"):
            if platform_info.get("lifetime"):
                status_lines.append("🚀 " + ("Платформа: навсегда" if ru else "Platform: lifetime"))
            elif platform_info.get("until"):
                from datetime import datetime
                try:
                    d = datetime.fromisoformat(platform_info["until"]).date().isoformat()
                    status_lines.append("🚀 " + (f"Платформа активна до {d}" if ru else f"Platform active until {d}"))
                except Exception:
                    status_lines.append("🚀 " + ("Платформа активна" if ru else "Platform active"))
        if credits_balance > 0:
            status_lines.append("🪙 " + (f"Кредитов ALEX: {credits_balance:,}".replace(",", " ")
                                          if ru else f"ALEX credits: {credits_balance:,}"))
        if status_lines:
            status_title = "Твой статус" if ru else "Your status"
            status_block = f"\n<blockquote><b>{status_title}</b>\n" + "\n".join(status_lines) + "</blockquote>"
        else:
            status_block = ""

        text = (
            "<b>⭐️ PolyGlotty · подписка и кредиты</b>\n\n"
            "<blockquote><b>🚀 Платформа</b>\n"
            "Курс A0–C2, экзамены, аналитика, безлимит карточек, roleplay и проверка текста. К каждому плану — стартовые кредиты ALEX.\n"
            "• 1 мес — <b>299 ⭐</b> (+30 кр)\n"
            "• 6 мес — <b>1 290 ⭐</b> (+150 кр · −28%)\n"
            "• Навсегда — <b>4 990 ⭐</b> (+500 кр)</blockquote>\n"
            "<blockquote><b>🪙 Кредиты ALEX</b>\n"
            "Отдельный баланс для чата. Тратятся за сообщение и не сгорают.\n"
            "Haiku 1 · Sonnet 4: 4 · Sonnet 4.6: 5 · Opus 4.1: 12 · Opus 4.7: 14 · Opus 4.8: 18 · Voice +5\n"
            "Пакеты: 100 · 500 (−20%) · 2 000 (−33%)</blockquote>"
            f"{status_block}\n\n"
            "<i>Оплата через Telegram Stars</i>"
        ) if ru else (
            "<b>⭐️ PolyGlotty · subscription & credits</b>\n\n"
            "<blockquote><b>🚀 Platform</b>\n"
            "A0–C2 course, exams, analytics, unlimited cards, roleplay and text check. Each plan includes starter ALEX credits.\n"
            "• 1 mo — <b>299 ⭐</b> (+30 cr)\n"
            "• 6 mo — <b>1 290 ⭐</b> (+150 cr · −28%)\n"
            "• Lifetime — <b>4 990 ⭐</b> (+500 cr)</blockquote>\n"
            "<blockquote><b>🪙 ALEX credits</b>\n"
            "Separate chat balance. Spent per message and never expire.\n"
            "Haiku 1 · Sonnet 4: 4 · Sonnet 4.6: 5 · Opus 4.1: 12 · Opus 4.7: 14 · Opus 4.8: 18 · Voice +5\n"
            "Packs: 100 · 500 (−20%) · 2 000 (−33%)</blockquote>"
            f"{status_block}\n\n"
            "<i>Telegram Stars only</i>"
        )
        await msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=plat_kb))
        return

# ══ LEGACY PREMIUM BUY CALLBACK (retired) ═════════════════════════════════
# The old Basic/Pro/Ultimate bundle is no longer sold. If a user taps an
# old inline button that still lingers in their chat history, send them to
# the current single subscription menu instead of an obsolete invoice.
@dp.callback_query(F.data.startswith("prem_buy:"))
async def cb_prem_buy(cb: CallbackQuery):
    try:
        await cb.answer()
    except Exception:
        pass
    await cmd_premium(cb.message)

# ── Invoice helpers (shared by callback + deep-link /start) ──────────────
async def _send_platform_invoice(uid: int, plan_id: str, ru: bool) -> bool:
    from aiogram.types import LabeledPrice
    plan = PLATFORM_PLANS.get(plan_id)
    if not plan:
        return False
    label = plan["label_ru"] if ru else plan["label_en"]
    stars = int(plan["stars"])
    desc = ("Подписка PolyGlotty Platform — курс, экзамены, статистика. ALEX-чат покупается отдельно кредитами."
            if ru else
            "PolyGlotty Platform subscription — course, exams, analytics. ALEX chat is sold separately as credits.")
    try:
        await bot.send_invoice(
            chat_id=uid,
            title=f"PolyGlotty {label}",
            description=desc,
            payload=f"platform:{plan['period']}:{uid}",
            currency="XTR",
            prices=[LabeledPrice(label=label, amount=stars)],
            protect_content=False,
        )
        return True
    except Exception as e:
        logger.error(f"send_invoice platform error: {e}")
        return False

async def _send_credits_invoice(uid: int, pack_id: str, ru: bool) -> bool:
    from aiogram.types import LabeledPrice
    pack = CREDIT_PACKS.get(pack_id)
    if not pack:
        return False
    label = pack["label_ru"] if ru else pack["label_en"]
    stars = int(pack["stars"])
    desc = ("Кредиты ALEX. Тратятся за каждое сообщение чату. Не сгорают."
            if ru else
            "ALEX credits. Spent per chat message. Never expire.")
    try:
        await bot.send_invoice(
            chat_id=uid,
            title=f"PolyGlotty {label}",
            description=desc,
            payload=f"credits:{pack['credits']}:{uid}",
            currency="XTR",
            prices=[LabeledPrice(label=label, amount=stars)],
            protect_content=False,
        )
        return True
    except Exception as e:
        logger.error(f"send_invoice credits error: {e}")
        return False

# ══ PLATFORM SUBSCRIPTION BUY (Stars) ════════════════════════════════════
@dp.callback_query(F.data.startswith("plat_buy:"))
async def cb_plat_buy(cb: CallbackQuery):
    parts = cb.data.split(":")
    plan_id = parts[1] if len(parts) > 1 else "plat_1m"
    uid = cb.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"
    if plan_id not in PLATFORM_PLANS:
        await cb.answer("Invalid plan", show_alert=True); return
    await cb.answer()
    ok = await _send_platform_invoice(uid, plan_id, ru)
    if not ok:
        await bot.send_message(uid, "Ошибка платежа. Попробуй позже." if ru else "Payment error. Try later.")

# ══ ALEX CREDITS BUY (Stars) ═════════════════════════════════════════════
@dp.callback_query(F.data.startswith("credit_buy:"))
async def cb_credit_buy(cb: CallbackQuery):
    parts = cb.data.split(":")
    pack_id = parts[1] if len(parts) > 1 else "credits_100"
    uid = cb.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"
    if pack_id not in CREDIT_PACKS:
        await cb.answer("Invalid pack", show_alert=True); return
    await cb.answer()
    ok = await _send_credits_invoice(uid, pack_id, ru)
    if not ok:
        await bot.send_message(uid, "Ошибка платежа. Попробуй позже." if ru else "Payment error. Try later.")

# ══ CARD PAYMENT MENU ════════════════════════════════════════════════════
@dp.callback_query(F.data == "prem_card_menu")
async def cb_card_menu(cb: CallbackQuery):
    await cb.answer("Оплата доступна только через Telegram Stars." if (await get_lang(cb.from_user.id) or "ru") == "ru" else "Payment is available through Telegram Stars only.", show_alert=True)

@dp.callback_query(F.data.startswith("prem_card:"))
async def cb_card_buy(cb: CallbackQuery):
    await cb.answer("Оплата доступна только через Telegram Stars." if (await get_lang(cb.from_user.id) or "ru") == "ru" else "Payment is available through Telegram Stars only.", show_alert=True)

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

    # ── New modular billing routes ────────────────────────────────────
    # payload format:
    #   premium:<plan_id>:<uid>      (legacy bundle, still allowed)
    #   platform:<period>:<uid>      (1m / 6m / lifetime)
    #   credits:<n>:<uid>            (ALEX credit pack)
    head = payload.split(":", 1)[0] if payload else ""
    if head == "platform":
        try:
            parts = payload.split(":")
            period = parts[1] if len(parts) > 1 else "1m"
            granted = await grant_platform_via_server(uid, period)
            label = {"1m":"1 месяц" if ru else "1 month",
                     "6m":"6 месяцев" if ru else "6 months",
                     "lifetime":"навсегда" if ru else "lifetime"}.get(period, period)
            if not granted:
                await msg.answer(("Оплата прошла, но доступ не активировался. Напиши /paysupport." if ru
                                  else "Payment succeeded but access did not activate. Use /paysupport."),
                                 parse_mode="HTML")
                return
            # ── Self-contained value: each Platform purchase ships with
            #    starter ALEX credits so the user can use the chat right
            #    away without a second purchase.
            starter_credits = 0
            plan_id = next((pid for pid, p in PLATFORM_PLANS.items() if p["period"] == period), None)
            if plan_id and PLATFORM_PLANS[plan_id].get("starter_credits"):
                starter_credits = int(PLATFORM_PLANS[plan_id]["starter_credits"])
                try:
                    await grant_credits_via_server(uid, starter_credits)
                except Exception as e:
                    logger.warning("starter credit grant failed uid=%s credits=%s: %s", uid, starter_credits, e)
            starter_line_ru = f"\n🪙 +<b>{starter_credits}</b> стартовых кредитов ALEX зачислено." if starter_credits else ""
            starter_line_en = f"\n🪙 +<b>{starter_credits}</b> starter ALEX credits added." if starter_credits else ""
            text = (
                f"🎉 <b>Оплата прошла</b>\n\n"
                f"🚀 <b>Платформа PolyGlotty</b> активна: <b>{label}</b>.\n"
                f"Курс, экзамены, расширенные карточки и аналитика — открыты.{starter_line_ru}\n\n"
                f"Кредиты ALEX докупаются отдельно в /premium, когда захочешь больше чата."
                if ru else
                f"🎉 <b>Payment successful</b>\n\n"
                f"🚀 <b>PolyGlotty Platform</b> is active: <b>{label}</b>.\n"
                f"Course, exams, expanded cards and analytics are unlocked.{starter_line_en}\n\n"
                f"Top up ALEX credits in /premium when you want more chat."
            )
            await msg.answer(text, parse_mode="HTML")
            logger.info("Platform granted uid=%s period=%s starter_credits=%s", uid, period, starter_credits)
        except Exception as e:
            logger.error(f"platform payment handler error: {e}")
            await msg.answer("✓ Платёж принят." if ru else "✓ Payment received.")
        return

    if head == "credits":
        try:
            parts = payload.split(":")
            credits = int(parts[1]) if len(parts) > 1 else 0
            granted = await grant_credits_via_server(uid, credits)
            if not granted:
                await msg.answer(("Оплата прошла, но кредиты не зачислились. Напиши /paysupport." if ru
                                  else "Payment succeeded but credits were not added. Use /paysupport."),
                                 parse_mode="HTML")
                return
            text = (
                f"🎉 <b>Кредиты зачислены</b>\n\n"
                f"🪙 +<b>{credits}</b> кредитов ALEX в твой пул.\n\n"
                f"Кредиты тратятся за каждое сообщение и не сгорают."
                if ru else
                f"🎉 <b>Credits added</b>\n\n"
                f"🪙 +<b>{credits}</b> ALEX credits in your pool.\n\n"
                f"Credits are spent per message and never expire."
            )
            await msg.answer(text, parse_mode="HTML")
            logger.info("Credits granted uid=%s credits=%s", uid, credits)
        except Exception as e:
            logger.error(f"credits payment handler error: {e}")
            await msg.answer("✓ Платёж принят." if ru else "✓ Payment received.")
        return

    # ── Legacy bundle path (premium:<plan_id>:<uid>) ──────────────────
    try:
        parts = payload.split(":")
        plan_id = parts[1] if len(parts) > 1 else "basic"
        plan = PREMIUM_PLANS.get(plan_id, PREMIUM_PLANS["basic"])
        months = plan["months"]
        label = plan["label_ru"] if ru else plan["label_en"]
        tier = plan.get("tier", "basic")
        tier_icon = {"basic":"💠","pro":"💎","ultimate":"🚀"}.get(tier,"💠")
        tier_scope_ru = {
            "basic": "ALEX Chat, Sonnet 4, голосовые ответы и AI-подсказки активны.",
            "pro": "Pro-функции, Sonnet 4.6, roleplay, проверка текста и отчёты активны.",
            "ultimate": "Ultimate-функции, TOEFL, Opus, длинная история и максимум лимитов активны.",
        }.get(tier, "Функции подписки активны.")
        tier_scope_en = {
            "basic": "ALEX Chat, Sonnet 4, voice replies and AI hints are active.",
            "pro": "Pro features, Sonnet 4.6, roleplay, text check and reports are active.",
            "ultimate": "Ultimate features, TOEFL, Opus, long history and max limits are active.",
        }.get(tier, "Subscription features are active.")

        # Grant premium in database via server, with direct DB fallback.
        granted = await grant_premium_via_server(uid, months, tier)
        if not granted:
            await msg.answer(
                "Оплата прошла, но доступ не активировался автоматически. Напиши /paysupport — я проверю подписку вручную."
                if ru else
                "Payment succeeded, but access was not activated automatically. Send /paysupport and I will check it manually.",
                parse_mode="HTML",
            )
            return

        # Confirm to user
        text = (
            f"✓ <b>Оплата прошла</b>\n\n"
            f"{tier_icon} ALEX Subscriptions <b>{tier.upper()}</b> активирован на <b>{label}</b>\n\n"
            f"{tier_scope_ru}\n\n"
            f"Открой приложение — подписка уже синхронизирована.\n\n"
            f"Спасибо за поддержку."
        ) if ru else (
            f"✓ <b>Payment successful</b>\n\n"
            f"{tier_icon} ALEX Subscriptions <b>{tier.upper()}</b> activated for <b>{label}</b>\n\n"
            f"{tier_scope_en}\n\n"
            f"Open the app — your subscription is already synced.\n\n"
            f"Thank you for your support."
        )
        await msg.answer(text, parse_mode="HTML")
        logger.info(f"Premium granted: uid={uid} plan={plan_id} months={months}")

    except Exception as e:
        logger.error(f"Payment success handler error: {e}")
        await msg.answer("✓ Payment received. Premium activated." if not ru else "✓ Оплата получена. Premium активирован.")

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
        await msg.answer(f"✓ Granted {months} months of Premium to uid {target_uid}")
    except Exception as e:
        await msg.answer(f"{ICON['warn']} Error: {e}")

async def main():
    await db_init()
    await schedule_all()
    scheduler.start()
    logger.info("PolyGlotty bot started")

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
