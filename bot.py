"""
╔══════════════════════════════════════════════════════════════════╗
   LinguaMax · ALEX v4 — Ultimate Edition
   
   НОВОЕ в v4:
   ✅ PostgreSQL (Railway) + SQLite fallback
   ✅ Tone Editor — 5 стилей переписывания фразы
   ✅ Whisper STT — анализ произношения из голосовых
   ✅ Cultural Idioms — сценарии с идиомами + SM-2
   ✅ Smart Roleplay — персонализация по профессии
   ✅ Vision Learning — учёба по фото
   ✅ TOEFL Listening — лекции И диалоги (чередуются)
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import base64
import json
import logging
import os
import random
import re

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    KeyboardButton, Message,
    PhotoSize, ReplyKeyboardMarkup, Voice,
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
    set_premium, check_premium,
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

# ══ ADMIN WHITELIST (free lifetime premium) ══════════════════════════════════
# Добавь сюда свои Telegram UID — они получат бесплатный Premium навсегда
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
        "stars": 99, "months": 1,
        "label_ru": "Basic · 1 мес", "label_en": "Basic · 1 mo",
        "tier": "basic",
        "buffs_ru": "✅ 40 сообщений в день\n✅ Голосовые ответы ALEX\n✅ 5 grammar games в день",
        "buffs_en": "✅ 40 messages/day\n✅ Voice replies from ALEX\n✅ 5 grammar games/day",
        "msg_limit": 40, "history": 30, "max_tokens": 1000,
    },
    "pro": {
        "stars": 249, "months": 3,
        "label_ru": "Pro · 3 мес", "label_en": "Pro · 3 mo",
        "tier": "pro",
        "buffs_ru": "✅ Безлимит сообщений\n✅ Голосовые ответы ALEX\n✅ Безлимит grammar games\n✅ Все сценки и roleplay\n✅ Подробный анализ ошибок\n✅ 🎭 VIP личности (Harvard, BBC)",
        "buffs_en": "✅ Unlimited messages\n✅ Voice replies from ALEX\n✅ Unlimited grammar games\n✅ All scenarios & roleplay\n✅ Detailed error analysis\n✅ 🎭 VIP personas (Harvard, BBC)",
        "msg_limit": 9999, "history": 50, "max_tokens": 1500,
    },
    "ultimate": {
        "stars": 499, "months": 12,
        "label_ru": "Ultimate · 1 год", "label_en": "Ultimate · 1 yr",
        "tier": "ultimate",
        "buffs_ru": "✅ Всё из Pro\n✅ Приоритет ответа (быстрее)\n✅ TOEFL Mock Exams\n✅ Персональный план обучения\n✅ Ранний доступ к новым фичам\n✅ 💎 Эксклюзивные темы",
        "buffs_en": "✅ Everything in Pro\n✅ Priority response (faster)\n✅ TOEFL Mock Exams\n✅ Personal study plan\n✅ Early access to new features\n✅ 💎 Exclusive themes",
        "msg_limit": 9999, "history": 80, "max_tokens": 2000,
    },
}
MODEL         = "claude-haiku-4-5"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

bot       = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp        = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

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
            headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": MODEL, "max_tokens": max_tokens, "system": system, "messages": messages},
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

    msgs_ru = ["📚 <b>Время английского!</b>","🔥 <b>Не теряй прогресс!</b>","⚡️ <b>Ежедневная практика = беглый English.</b>"]
    msgs_en = ["📚 <b>Time to practice!</b>","🔥 <b>Keep your streak alive!</b>","⚡️ <b>Daily practice = fluency.</b>"]
    text = random.choice(msgs_ru if lang=="ru" else msgs_en) + interest_hint
    if streak > 2: text += f"\n\n🔥 Streak: <b>{streak}</b>!"
    if due_cnt:    text += f"\n📅 <b>{due_cnt}</b> {'слов для повторения' if lang=='ru' else 'words due'} → /vocab"
    if due_idioms: text += f"\n🗣 <b>{due_idioms}</b> {'идиом для повторения' if lang=='ru' else 'idioms due'} → /vocab"
    try: await bot.send_message(uid, text)
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

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid  = message.from_user.id
    name = message.from_user.first_name or "Student"
    await upsert_user(uid, name)

    # Handle deep links: /start premium, /start ref_XXXXX
    args = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if args == "premium":
        # Route to premium command
        await cmd_premium(message)
        return
    if args.startswith("ref_"):
        # Referral — grant bonus XP to referrer
        try:
            ref_uid = int(args.replace("ref_",""))
            await add_xp(ref_uid, 500)
            lang_ref = await get_lang(ref_uid)
            ru_ref = lang_ref == "ru"
            await bot.send_message(ref_uid,
                f"🎉 {'Твой друг зарегистрировался по ссылке! +500 XP тебе!' if ru_ref else 'Your friend joined via your link! +500 XP for you!'}")
        except Exception:
            pass

    scheduler.add_job(send_weekly_report,"cron",day_of_week="sun",hour=19,args=[uid],id=f"weekly_{uid}",replace_existing=True)

    # Grant free premium if admin
    if await is_admin(uid):
        await grant_premium_via_server(uid, 999)

    user = await get_user(uid)
    if not user or not user.get("lang"):
        await message.answer("🌍 Choose language / Выбери язык:", reply_markup=lang_kb())
        return
    lang = await get_lang(uid)
    interests = await get_interests(uid)
    ctx_msg = f"\n\n💡 {'Продолжим работу над' if lang=='ru' else 'Continuing with'} <b>{interests.split(',')[0].strip()}</b>!" if interests else ""
    await message.answer(
        f"<b>{'Привет' if lang=='ru' else 'Hey'}, {name}!</b> 👋\n\n"
        f"Я <b>ALEX</b> — {'твой AI-репетитор английского.' if lang=='ru' else 'your AI English tutor.'}{ctx_msg}\n\n"
        f"<i>{'Выбери с чего начать 👇' if lang=='ru' else 'Choose where to start 👇'}</i>",
        reply_markup=main_kb(lang)
    )
    await message.answer("🎯 <b>Level:</b>", reply_markup=level_kb())

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
    await m.answer("🎭 <b>Roleplay:</b>", reply_markup=roleplay_kb(lang))

@dp.message(Command("story"))
async def cmd_story(m: Message):
    lang = await get_lang(m.from_user.id)
    await m.answer("🎮 <b>Story Quest:</b>", reply_markup=story_kb(lang))

@dp.message(Command("debate"))
async def cmd_debate(m: Message):
    uid  = m.from_user.id
    lang = await get_lang(uid)
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
    await m.answer("⏰ Set daily reminder:", reply_markup=remind_kb())

@dp.message(Command("help"))
async def cmd_help(m: Message):
    lang = await get_lang(m.from_user.id)
    await m.answer(
        "<b>LinguaMax ALEX v4</b>\n\n"
        "/lesson — грамматика\n/vocab — словарь\n/roleplay — ролевые диалоги\n"
        "/story — Story Quest RPG\n/debate — дебаты\n/test — тесты\n"
        "/toefl — TOEFL (лекции + диалоги)\n/shadow — shadowing + произношение\n"
        "/tone — редактор тона фразы\n/writing — проверка текста\n"
        "/sentence — конструктор предложений\n/idioms — идиомы\n"
        "/talk — разговорная практика\n/stats — прогресс\n"
        "/mistakes — мои ошибки\n/interests — интересы\n"
        "/profession — профессия (для ролеплей)\n"
        "/remind — напоминания\n/reset — сброс\n\n"
        "📸 Фото → анализ текста и учёба\n"
        "🎤 Голосовое → анализ произношения\n"
        "🔤 @бот слово → перевод в любом чате"
        if lang=="ru" else
        "<b>LinguaMax ALEX v4</b>\n\n"
        "/lesson · /vocab · /roleplay · /story · /debate\n"
        "/test · /toefl · /shadow · /tone · /writing\n"
        "/sentence · /idioms · /talk · /stats · /mistakes\n"
        "/interests · /profession · /remind · /reset\n\n"
        "📸 Photo → visual learning\n"
        "🎤 Voice → pronunciation analysis\n"
        "🔤 @bot word → translate in any chat"
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
    name = cb.from_user.first_name or "Student"
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
        await cb.answer(); await cb.message.edit_text("❌ Reminders disabled.")
    else:
        await update_user(uid, remind_time=data)
        try:
            h, m = map(int, data.split(":"))
            scheduler.add_job(send_reminder,"cron",hour=h,minute=m,args=[uid],id=f"remind_{uid}",replace_existing=True)
        except Exception as e: logger.warning(e)
        await cb.answer(f"✅ {data}")
        await cb.message.edit_text(f"✅ <b>Reminder: {data}</b> 📚")

# ══════════════════════════════════════════════════════════════════
#  ФОТО — Vision Learning
# ══════════════════════════════════════════════════════════════════

@dp.message(F.photo)
async def handle_photo(message: Message):
    uid  = message.from_user.id
    lang = await get_lang(uid)
    photo = message.photo[-1]
    fi = await bot.get_file(photo.file_id)
    fb = await bot.download_file(fi.file_path)
    pb = fb.read() if hasattr(fb,"read") else bytes(fb)
    await message.answer("📸 Analyzing..." if lang=="en" else "📸 Анализирую...")
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await analyze_photo_vision(uid, pb)
    await message.answer(reply)
    log_session(uid, "photo_vision")

# ══════════════════════════════════════════════════════════════════
#  ГОЛОСОВЫЕ — STT + произношение
# ══════════════════════════════════════════════════════════════════

@dp.message(F.voice)
async def handle_voice(message: Message):
    uid   = message.from_user.id
    lang  = await get_lang(uid)
    state = waiting.get(uid,"")

    voice: Voice = message.voice
    fi = await bot.get_file(voice.file_id)
    fb = await bot.download_file(fi.file_path)
    audio_bytes = fb.read() if hasattr(fb,"read") else bytes(fb)

    await bot.send_chat_action(message.chat.id, "typing")

    # Если в режиме shadowing — анализируем произношение
    if state == "shadowing":
        phrase = get_ctx(uid).get("shadow_phrase","")
        transcribed = await transcribe_audio(audio_bytes, "voice.ogg")

        if transcribed and phrase:
            waiting.pop(uid, None)
            analysis = analyze_pronunciation(phrase, transcribed)
            report   = format_pronunciation_report(analysis, lang)
            await message.answer(report, reply_markup=shadowing_kb(lang))
            await add_xp(uid, 15)
        elif transcribed:
            # Нет фразы для сравнения — просто транскрибируем
            waiting.pop(uid, None)
            reply = await ask_alex(uid, transcribed, mode="correction",
                extra="The student sent a voice message. Transcription: correct any errors and respond.")
            await message.answer(f"🎤 <i>{transcribed}</i>\n\n{reply}")
        else:
            await message.answer(
                "⚠️ Не смог распознать речь. Проверь OPENAI_API_KEY в Railway Variables." if lang=="ru"
                else "⚠️ Couldn't transcribe. Check OPENAI_API_KEY in Railway Variables."
            )
        return

    # Обычное голосовое — транскрибируем и обрабатываем как текст
    transcribed = await transcribe_audio(audio_bytes, "voice.ogg")
    if transcribed:
        await message.answer(f"🎤 <i>{transcribed}</i>")
        await bot.send_chat_action(message.chat.id, "typing")
        # Определяем режим из состояния
        mode_map = {"lesson_active":"grammar","speaking_active":"speaking","toefl_active":"toefl",
                    "roleplay_active":"roleplay","story_active":"story","debate_active":"debate"}
        mode = mode_map.get(state, "correction")
        reply = await ask_alex(uid, transcribed, mode=mode)
        await message.answer(reply)
        log_session(uid, "voice_message")
    else:
        await message.answer(
            "🎤 Голосовые без Whisper не работают. Добавь OPENAI_API_KEY в Railway Variables." if lang=="ru"
            else "🎤 Voice messages need Whisper. Add OPENAI_API_KEY in Railway Variables."
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

@dp.message(F.text)
async def handle_text(message: Message):
    uid   = message.from_user.id
    lang  = await get_lang(uid)
    text  = message.text.strip()
    state = waiting.get(uid,"")

    # Кнопки меню
    menu   = MENU_RU if lang=="ru" else MENU_EN
    action = menu.get(text)
    if action:
        handlers = {
            "lesson":cmd_lesson,"vocab":cmd_vocab,"roleplay":cmd_roleplay,"story":cmd_story,
            "test":cmd_test,"toefl":cmd_toefl,"writing":cmd_writing,"tone":cmd_tone,
            "talk":cmd_talk,"debate":cmd_debate,"idioms":cmd_idioms,"mistakes":cmd_mistakes,"stats":cmd_stats,
        }
        h = handlers.get(action)
        if h: await h(message)
        return

    # Состояния
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
        await message.answer(
            f"💼 {'Профессия сохранена:' if lang=='ru' else 'Profession saved:'} <b>{text}</b>\n\n"
            f"{'Теперь /roleplay → По моей профессии даст персональный сценарий!' if lang=='ru' else 'Now /roleplay → My Profession gives a personalized scenario!'}"
        )
        return

    if state == "rp_custom":
        waiting.pop(uid, None); clear_history(uid)
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, f"Start roleplay: {text}. Begin immediately in character.", mode="roleplay")
        await message.answer(reply)
        log_session(uid, "roleplay_custom"); waiting[uid] = "roleplay_active"; return

    if state == "writing":
        waiting.pop(uid, None)
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, f"3-layer analysis:\n\n{text}", mode="correction")
        await message.answer(reply)
        log_session(uid, "writing_check")
        if len(text) > 20: log_mistake(uid, text[:100], "See correction", "writing", "mixed")
        return

    if state == "tone_editor":
        waiting.pop(uid, None)
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, f"Analyze and rewrite in 5 tones:\n\n\"{text}\"", mode="tone_editor")
        await message.answer(reply)
        log_tone(uid, text, reply)
        log_session(uid, "tone_editor"); return

    if state == "shadowing":
        phrase = get_ctx(uid).get("shadow_phrase","")
        if phrase:
            match = sum(1 for a,b in zip(text.lower().split(), phrase.lower().split()) if a==b)
            total = len(phrase.split())
            pct   = int(match/total*100) if total else 0
            if pct >= 90:   icon, grade = "🏆", "Excellent!" if lang=="en" else "Отлично!"
            elif pct >= 75: icon, grade = "👍", "Good!" if lang=="en" else "Хорошо!"
            elif pct >= 55: icon, grade = "💪", "Almost!" if lang=="en" else "Почти!"
            else:           icon, grade = "🔄", "Try again" if lang=="en" else "Попробуй снова"
            await message.answer(
                f"🎙 {icon} <b>{grade}</b> — {pct}%\n"
                f"✅ <i>{phrase}</i>",
                reply_markup=shadowing_kb(lang)
            )
            await add_xp(uid, 10)
        waiting.pop(uid, None); clear_ctx(uid); return

    if state == "story_active":
        ctx  = get_ctx(uid)
        hp   = ctx.get("story_hp",100)
        score = ctx.get("story_score",0)
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode="story", extra=f"HP: {hp}/100, Score: {score}")
        if "HP:" in reply:
            try:
                new_hp = int(re.search(r'HP:\s*(\d+)', reply).group(1))
                set_ctx(uid, story_hp=new_hp)
                await update_story(uid, hp=new_hp)
            except Exception: pass
        await message.answer(reply); return

    if state in ("test_active","lesson_active","speaking_active","toefl_active",
                 "vocab_active","debate_active","roleplay_active","toefl_listening_answers"):
        mode_map = {
            "test_active":"test","lesson_active":"grammar","speaking_active":"speaking",
            "toefl_active":"toefl","vocab_active":"vocab","debate_active":"debate",
            "roleplay_active":"roleplay","toefl_listening_answers":"general",
        }
        mode = mode_map.get(state,"general")
        lower = text.lower()
        if any(p in lower for p in ["объясни иначе","explain differently","i don't get it","другой пример","new analogy"]):
            await bot.send_chat_action(message.chat.id, "typing")
            reply = await ask_alex(uid, "Explain using a completely different analogy or approach.", mode=mode)
            await message.answer(reply); return
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode=mode)
        await message.answer(reply); return

    # Автокоррекция английского
    en_ratio = sum(1 for c in text if c.isalpha() and ord(c)<128)/max(len(text),1)
    if en_ratio > 0.6 and len(text) > 8:
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode="correction",
            extra="If errors exist: correct them. If perfect: compliment + continue naturally.")
        await message.answer(reply)
        log_session(uid, "free_writing")
        if len(text) > 15: log_mistake(uid, text[:100], "See correction", "free writing", "mixed")
    else:
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode="general")
        await message.answer(reply)
        log_session(uid, "chat")

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
    """Check if user is admin (free premium)."""
    return uid in ADMIN_IDS

async def grant_premium_via_server(uid: int, months: int):
    """Call server API to grant premium."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"http://localhost:8080/api/premium/grant",
                json={"uid": uid, "months": months, "secret": BOT_SECRET}
            )
    except Exception as e:
        logger.error(f"grant_premium_via_server error: {e}")

# ══ /premium COMMAND ══════════════════════════════════════════════════════════
@dp.message(Command("premium"))
async def cmd_premium(msg: Message):
    uid = msg.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"

    # Admins get free premium automatically
    if await is_admin(uid):
        await grant_premium_via_server(uid, 999)  # 999 months ≈ lifetime
        text = (
            "👑 <b>Premium Ultimate активирован!</b>\n\n"
            "Ты в VIP-списке — всё бесплатно навсегда 🎉\n\n"
            "🎤 Голосовые ответы · 💬 Безлимит\n"
            "🎭 Все сценки · 📊 Анализ ошибок\n"
            "👑 VIP личности · 💎 Все темы"
        ) if ru else (
            "👑 <b>Premium Ultimate activated!</b>\n\n"
            "You're on the VIP list — everything is free forever 🎉\n\n"
            "🎤 Voice · 💬 Unlimited · 🎭 Roleplay\n"
            "📊 Error analysis · 👑 VIP personas · 💎 All themes"
        )
        await msg.answer(text, parse_mode="HTML")
        return

    # Check if already premium
    is_prem = await check_premium(uid)
    prem_badge = ""
    if is_prem:
        prem_badge = ("\n\n✅ <i>У тебя уже есть Premium. Можешь продлить!</i>" if ru
                      else "\n\n✅ <i>You already have Premium. You can extend!</i>")

    plans_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🟢 Basic — 99 ⭐/мес" if ru else f"🟢 Basic — 99 ⭐/mo",
            callback_data="prem_buy:basic"
        )],
        [InlineKeyboardButton(
            text=f"🔵 Pro — 249 ⭐/3 мес (-17%)" if ru else f"🔵 Pro — 249 ⭐/3mo (-17%)",
            callback_data="prem_buy:pro"
        )],
        [InlineKeyboardButton(
            text=f"💎 Ultimate — 499 ⭐/год (-58%)" if ru else f"💎 Ultimate — 499 ⭐/yr (-58%)",
            callback_data="prem_buy:ultimate"
        )],
    ])

    text = (
        "💎 <b>ALEX Premium</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "🟢 <b>Basic</b> — 99 ⭐/мес\n"
        "├ 40 сообщений в день\n"
        "├ Голосовые ответы ALEX\n"
        "└ 5 grammar games в день\n\n"
        "🔵 <b>Pro</b> — 249 ⭐/3 мес\n"
        "├ Безлимит сообщений\n"
        "├ Голос + все сценки\n"
        "├ Подробный анализ ошибок\n"
        "└ 🎭 VIP личности\n\n"
        "💎 <b>Ultimate</b> — 499 ⭐/год\n"
        "├ Всё из Pro\n"
        "├ TOEFL Mock Exams\n"
        "├ Персональный план\n"
        "└ 💎 Эксклюзивные темы\n"
        f"{prem_badge}\n\n"
        "⭐ Оплата через Telegram Stars"
    ) if ru else (
        "💎 <b>ALEX Premium</b>\n"
        "━━━━━━━━━━━━━━━━━\n\n"
        "🟢 <b>Basic</b> — 99 ⭐/mo\n"
        "├ 40 messages/day\n"
        "├ Voice replies from ALEX\n"
        "└ 5 grammar games/day\n\n"
        "🔵 <b>Pro</b> — 249 ⭐/3mo\n"
        "├ Unlimited messages\n"
        "├ Voice + all scenarios\n"
        "├ Detailed error analysis\n"
        "└ 🎭 VIP personas\n\n"
        "💎 <b>Ultimate</b> — 499 ⭐/yr\n"
        "├ Everything in Pro\n"
        "├ TOEFL Mock Exams\n"
        "├ Personal study plan\n"
        "└ 💎 Exclusive themes\n"
        f"{prem_badge}\n\n"
        "⭐ Payment via Telegram Stars"
    )
    await msg.answer(text, parse_mode="HTML", reply_markup=plans_kb)

# ══ PREMIUM BUY CALLBACK ═══════════════════════════════════════════════════
@dp.callback_query(F.data.startswith("prem_buy:"))
async def cb_prem_buy(cb: CallbackQuery):
    from aiogram.types import LabeledPrice
    plan_id = cb.data.split(":")[1]
    plan = PREMIUM_PLANS.get(plan_id)
    if not plan:
        await cb.answer("Invalid plan", show_alert=True)
        return

    uid = cb.from_user.id
    user_lang = await get_lang(uid) or "ru"
    ru = user_lang == "ru"

    label = plan["label_ru"] if ru else plan["label_en"]
    buffs = plan.get("buffs_ru","") if ru else plan.get("buffs_en","")
    desc_short = buffs.replace("✅ ","").replace("\n"," · ")[:240]
    desc = f"ALEX Premium — {label}\n{desc_short}"

    await cb.answer()
    try:
        await bot.send_invoice(
            chat_id=uid,
            title=f"ALEX Premium {label}",
            description=desc,
            payload=f"premium:{plan_id}:{uid}",
            currency="XTR",  # Telegram Stars
            prices=[LabeledPrice(label=f"Premium {label}", amount=plan["stars"])],
            protect_content=False,
        )
    except Exception as e:
        logger.error(f"send_invoice error: {e}")
        err = "Ошибка при создании платежа. Попробуй позже." if ru else "Payment error. Try again later."
        await bot.send_message(uid, err)

# ══ PRE-CHECKOUT ══════════════════════════════════════════════════════════
@dp.pre_checkout_query()
async def pre_checkout(pcq):
    """Always approve — Telegram Stars payments are instant."""
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
        await grant_premium_via_server(uid, months)

        # Confirm to user
        text = (
            f"🎉 <b>Оплата прошла!</b>\n\n"
            f"{tier_emoji} ALEX Premium <b>{tier.upper()}</b> активирован на <b>{label}</b>\n\n"
            f"Открой приложение — все функции разблокированы!\n\n"
            f"Спасибо за поддержку! 🙏"
        ) if ru else (
            f"🎉 <b>Payment successful!</b>\n\n"
            f"{tier_emoji} ALEX Premium <b>{tier.upper()}</b> activated for <b>{label}</b>\n\n"
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

    try:
        await dp.start_polling(bot)
    finally:
        await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
