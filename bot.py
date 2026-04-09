"""
╔══════════════════════════════════════════════════════════════════╗
   LinguaMax · ALEX v3 — Ultimate Edition
   
   НОВОЕ в v3:
   ✅ Smart Context Memory — автосохранение интересов
   ✅ Story Quest RPG (детектив, фэнтези, sci-fi, выживание)
   ✅ TOEFL Listening с реальным TTS аудио (gTTS / ElevenLabs)
   ✅ Inline-режим — перевод в любом чате
   ✅ Adaptive Difficulty — уровень меняется автоматически
   ✅ Debate FSM — 3 раунда дебатов с оценкой
   ✅ Конструктор предложений
   ✅ Shadowing режим — фраза → повтори → проверка
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
    ChosenInlineResult, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    KeyboardButton, Message,
    PhotoSize, ReplyKeyboardMarkup, Voice,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database import (
    db_init, db, get_user, get_lang, get_level, get_interests,
    upsert_user, update_user, add_xp, update_streak,
    get_streak_count, get_xp, get_rank,
    add_word, get_due_words, update_word_review, get_word_count,
    log_mistake, get_mistakes,
    log_session, get_full_stats,
    log_toefl, get_toefl_scores,
    save_interest, get_all_interests,
    track_complexity,
    start_story, get_active_story, update_story,
    LEVEL_ORDER,
)
from prompts import (
    build_system,
    ROLEPLAY_SCENARIOS, STORY_TYPES,
    LESSON_PROMPTS, VOCAB_PROMPTS,
    TEST_PROMPTS, TOEFL_PROMPTS, TALK_PROMPTS,
)
from tts import text_to_speech

load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
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

def add_message(uid: int, role: str, content):
    h = get_history(uid)
    h.append({"role": role, "content": content})
    if len(h) > 30: histories[uid] = h[-30:]

def clear_history(uid): histories[uid] = []
def set_ctx(uid, **kw): session_ctx.setdefault(uid, {}).update(kw)
def get_ctx(uid) -> dict: return session_ctx.get(uid, {})
def clear_ctx(uid): session_ctx.pop(uid, None)

FOOTER = "\n\n<i>──────────────────────────────────</i>\n<i>/lesson · /vocab · /test · /toefl · /roleplay · /story · /help</i>"

# ══════════════════════════════════════════════════════════════════
#  ANTHROPIC API + Smart Interest Detection
# ══════════════════════════════════════════════════════════════════

INTEREST_TAG = re.compile(r'\[SAVE_INTEREST:\s*([^\]]+)\]')

async def ask_alex(uid: int, user_text: str, mode: str = "general", extra: str = "",
                   no_footer: bool = False) -> str:
    add_message(uid, "user", user_text)
    system = build_system(uid, mode)
    if extra: system += f"\n\n{extra}"

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": 1500, "system": system, "messages": get_history(uid)},
            )
            data = r.json()
            if "error" in data:
                return f"⚠️ <code>{data['error'].get('message','')[:150]}</code>"
            reply = data["content"][0]["text"].strip()
    except Exception as e:
        return f"⚠️ Error. Try again.\n<i>{str(e)[:80]}</i>"

    # Smart Interest Detection
    matches = INTEREST_TAG.findall(reply)
    for interest in matches:
        interest = interest.strip()
        if save_interest(uid, interest, source="auto"):
            logger.info(f"Auto-saved interest for {uid}: {interest}")

    # Убираем теги из ответа
    clean_reply = INTEREST_TAG.sub("", reply).strip()

    # Адаптивная сложность
    word_count = len(user_text.split())
    has_complex = any(p in user_text.lower() for p in [
        "although","however","therefore","furthermore","nevertheless",
        "consequently","whereas","provided that","in spite of","would have"
    ])
    if word_count > 20 or has_complex:
        level_change = track_complexity(uid, True)
    elif word_count < 5:
        level_change = track_complexity(uid, False)
    else:
        level_change = None

    add_message(uid, "assistant", clean_reply)

    result = clean_reply + ("" if no_footer else FOOTER)

    # Уведомление о смене уровня
    if level_change:
        direction, new_level = level_change.split(":")
        if direction == "up":
            result += f"\n\n🎉 <b>Уровень повышен до {new_level}!</b> Твои тексты стали заметно сложнее."
        else:
            result += f"\n\n💡 <b>Уровень скорректирован до {new_level}</b> для более эффективной практики."

    return result


async def ask_alex_raw(prompt: str, system: str) -> str:
    """Прямой запрос без истории — для TOEFL JSON генерации и инлайна."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": 2000, "system": system,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            data = r.json()
            if "error" in data: return ""
            return data["content"][0]["text"].strip()
    except Exception as e:
        logger.error(f"ask_alex_raw: {e}")
        return ""


async def analyze_photo(uid: int, photo_bytes: bytes) -> str:
    b64  = base64.standard_b64encode(photo_bytes).decode()
    lang = get_lang(uid)
    prompt = (
        "Analyze the English text in this image:\n"
        "📸 <b>Recognized text:</b> [extract all visible text]\n"
        "💡 <b>Key vocabulary:</b> [5 most useful phrases with translations]\n"
        "📚 <b>Grammar notes:</b> [interesting grammar points]\n"
        "✅ <b>Corrections:</b> [any errors found]\n"
        "If it's a UI/interface screenshot: explain the technical English terms in context."
    )
    system = build_system(uid, "correction")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": MODEL, "max_tokens": 1500, "system": system,
                    "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text", "text": prompt},
                    ]}],
                },
            )
            data = r.json()
            if "error" in data: return f"⚠️ <code>{data['error'].get('message','')[:120]}</code>"
            return data["content"][0]["text"].strip() + FOOTER
    except Exception as e:
        return f"⚠️ <i>{str(e)[:100]}</i>"

# ══════════════════════════════════════════════════════════════════
#  TOEFL LISTENING С АУДИО
# ══════════════════════════════════════════════════════════════════

async def run_toefl_listening(uid: int, message: Message):
    lang  = get_lang(uid)
    level = get_level(uid)

    # Шаг 1: Генерируем контент через JSON промпт
    status_msg = await message.answer(
        "🎧 <b>Генерирую лекцию...</b>" if lang=="ru" else "🎧 <b>Generating lecture...</b>"
    )

    toefl_system = build_system(uid, "toefl_json")
    raw = await ask_alex_raw(f"[TOEFL_GENERATE_LEVEL: {level}]", toefl_system)

    # Парсим JSON
    try:
        # Ищем JSON в ответе
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON found")
        data = json.loads(json_match.group())
        transcript = data["transcript"]
        questions  = data["questions"]
    except Exception as e:
        logger.error(f"TOEFL JSON parse error: {e}\nRaw: {raw[:200]}")
        await status_msg.edit_text(
            "⚠️ Ошибка генерации. Попробуй ещё раз." if lang=="ru" else "⚠️ Generation error. Try again."
        )
        return

    # Шаг 2: TTS — конвертируем транскрипт в аудио
    await status_msg.edit_text(
        "🔊 <b>Синтезирую речь...</b>" if lang=="ru" else "🔊 <b>Synthesizing audio...</b>"
    )

    audio_bytes = await text_to_speech(transcript, lang="en")

    if audio_bytes:
        # Отправляем аудио
        audio_file = BufferedInputFile(audio_bytes, filename="lecture.mp3")
        await message.answer_voice(
            audio_file,
            caption=(
                "🎧 <b>Прослушай лекцию.</b>\n"
                "Когда закончишь — нажми кнопку ниже для вопросов."
                if lang == "ru" else
                "🎧 <b>Listen to the lecture.</b>\n"
                "When done — press the button below for questions."
            ),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Готов отвечать" if lang=="ru" else "✅ Ready for questions",
                    callback_data="toefl_q_start"
                )
            ]])
        )
    else:
        # Fallback: показываем транскрипт для чтения
        await message.answer(
            f"🎧 <b>{'Прочитай лекцию внимательно (аудио недоступно):' if lang=='ru' else 'Read the lecture carefully (audio unavailable):'}</b>\n\n"
            f"<i>{transcript}</i>\n\n"
            f"{'Когда запомнишь — нажми кнопку:' if lang=='ru' else 'When ready — press the button:'}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Готов" if lang=="ru" else "✅ Ready",
                    callback_data="toefl_q_start"
                )
            ]])
        )

    # Сохраняем данные сессии
    set_ctx(uid, toefl_transcript=transcript, toefl_questions=questions,
            toefl_answers={}, toefl_q_idx=0)
    await status_msg.delete()
    log_session(uid, "toefl_listening")


async def send_toefl_question(uid: int, message: Message, idx: int):
    """Отправляет вопрос TOEFL с inline кнопками."""
    ctx       = get_ctx(uid)
    questions = ctx.get("toefl_questions", [])
    lang      = get_lang(uid)

    if idx >= len(questions):
        await finish_toefl_listening(uid, message)
        return

    q = questions[idx]
    text = f"❓ <b>Question {idx+1}/{len(questions)}</b>\n\n{q['question_text']}"
    buttons = [
        [InlineKeyboardButton(text=f"{k}: {v[:50]}", callback_data=f"toefl_ans_{idx}_{k}")]
        for k, v in q["options"].items()
    ]
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


async def finish_toefl_listening(uid: int, message: Message):
    """Проверяет ответы и показывает результат."""
    ctx       = get_ctx(uid)
    questions = ctx.get("toefl_questions", [])
    answers   = ctx.get("toefl_answers", {})
    transcript = ctx.get("toefl_transcript", "")
    lang      = get_lang(uid)

    correct = sum(1 for i, q in enumerate(questions)
                  if answers.get(str(i)) == q["correct_answer"])

    # Просим ALEX проверить и объяснить
    check_prompt = (
        f"[TOEFL_CHECK_ANSWERS]\n"
        f"Transcript: {transcript[:500]}\n"
        f"Questions and answers:\n"
    )
    for i, q in enumerate(questions):
        user_ans = answers.get(str(i), "?")
        correct_ans = q["correct_answer"]
        check_prompt += f"Q{i+1}: {q['question_text']}\nUser: {user_ans} | Correct: {correct_ans}\n"
        if user_ans != correct_ans:
            check_prompt += f"Explanation: {q.get('explanation','')}\n"
        check_prompt += "\n"

    analysis = await ask_alex_raw(check_prompt, build_system(uid, "toefl_json"))

    score_text = (
        f"🎧 <b>TOEFL Listening — Результат</b>\n\n"
        f"✅ Правильно: <b>{correct}/{len(questions)}</b>\n\n"
        f"{analysis}"
        if lang == "ru" else
        f"🎧 <b>TOEFL Listening — Result</b>\n\n"
        f"✅ Correct: <b>{correct}/{len(questions)}</b>\n\n"
        f"{analysis}"
    )
    await message.answer(score_text[:4000])
    log_toefl(uid, "listening", correct, len(questions))
    clear_ctx(uid)
    waiting.pop(uid, None)


# ══════════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════════

def main_kb(lang: str) -> ReplyKeyboardMarkup:
    if lang == "ru":
        rows = [
            ["📚 Урок грамматики",   "📝 Словарь"],
            ["🎭 Ролевой диалог",    "🎮 Story Quest"],
            ["✅ Тест",              "🎓 TOEFL"],
            ["✍️ Проверить текст",   "💬 Разговор"],
            ["⚔️ Дебаты",           "🗣 Идиомы"],
            ["❌ Мои ошибки",        "📊 Прогресс"],
        ]
        ph = "Пиши по-английски — ALEX проверит..."
    else:
        rows = [
            ["📚 Grammar Lesson",   "📝 Vocabulary"],
            ["🎭 Roleplay",         "🎮 Story Quest"],
            ["✅ Test",             "🎓 TOEFL"],
            ["✍️ Check Writing",    "💬 Speaking"],
            ["⚔️ Debate",          "🗣 Idioms"],
            ["❌ My Mistakes",      "📊 Progress"],
        ]
        ph = "Write in English — ALEX will check..."
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True, input_field_placeholder=ph,
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
    labels = {
        "lesson_tenses":       ("⏰ Времена глагола",          "⏰ Verb Tenses"),
        "lesson_conditionals": ("🔀 Условные предложения",     "🔀 Conditionals"),
        "lesson_modal":        ("💭 Модальные глаголы",        "💭 Modal Verbs"),
        "lesson_passive":      ("🔄 Пассивный залог",          "🔄 Passive Voice"),
        "lesson_articles":     ("📌 Артикли",                  "📌 Articles"),
        "lesson_prepositions": ("📍 Предлоги",                 "📍 Prepositions"),
        "lesson_phrasal":      ("🔗 Фразовые глаголы",         "🔗 Phrasal Verbs"),
        "lesson_reported":     ("💬 Косвенная речь",           "💬 Reported Speech"),
        "lesson_subjunctive":  ("🌙 Сослагательное",           "🌙 Subjunctive"),
        "lesson_inversion":    ("🔁 Инверсия (C1-C2)",         "🔁 Inversion (C1-C2)"),
    }
    rows = [[InlineKeyboardButton(text=v[0 if lang=="ru" else 1], callback_data=k)] for k,v in labels.items()]
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def vocab_kb(lang):
    labels = {
        "vocab_new":         ("🆕 Новые слова",          "🆕 New Words"),
        "vocab_review":      ("🔄 Повторение SM-2",      "🔄 SM-2 Review"),
        "vocab_flashcards":  ("🃏 Флэш-карточки",        "🃏 Flashcards"),
        "vocab_collocations":("🤝 Коллокации",           "🤝 Collocations"),
        "vocab_idioms_adv":  ("🗣 Продвинутые идиомы",   "🗣 Advanced Idioms"),
        "vocab_topic":       ("📂 По теме",              "📂 By Topic"),
        "daily_quiz":        ("📅 Ежедневный квиз",      "📅 Daily Quiz"),
    }
    rows = [[InlineKeyboardButton(text=v[0 if lang=="ru" else 1], callback_data=k)] for k,v in labels.items()]
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def test_kb(lang):
    labels = {
        "test_grammar":   ("📐 Грамматика",           "📐 Grammar"),
        "test_vocab":     ("📝 Лексика",              "📝 Vocabulary"),
        "test_reading":   ("📖 Чтение",               "📖 Reading"),
        "test_writing":   ("✍️ Письмо",               "✍️ Writing"),
        "test_mixed":     ("🎲 Смешанный",            "🎲 Mixed"),
        "test_placement": ("🔍 Определить уровень",   "🔍 Placement Test"),
    }
    rows = [[InlineKeyboardButton(text=v[0 if lang=="ru" else 1], callback_data=k)] for k,v in labels.items()]
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def toefl_kb(lang):
    labels = {
        "toefl_reading":   ("📖 Reading",              "📖 Reading"),
        "toefl_listening": ("🎧 Listening + Audio",    "🎧 Listening + Audio"),
        "toefl_speaking1": ("🗣 Speaking Independent", "🗣 Speaking Independent"),
        "toefl_speaking2": ("🗣 Speaking Integrated",  "🗣 Speaking Integrated"),
        "toefl_writing1":  ("✍️ Writing Independent",  "✍️ Writing Independent"),
        "toefl_writing2":  ("✍️ Writing Integrated",   "✍️ Writing Integrated"),
        "toefl_full":      ("🏆 Полный мини-тест",     "🏆 Full Mini-Test"),
        "toefl_strategy":  ("💡 Стратегии",            "💡 Strategies"),
        "toefl_score":     ("📊 Мои баллы",            "📊 My Scores"),
    }
    rows = [[InlineKeyboardButton(text=v[0 if lang=="ru" else 1], callback_data=k)] for k,v in labels.items()]
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def talk_kb(lang):
    labels = {
        "talk_daily":     ("☀️ Повседневная жизнь", "☀️ Daily Life"),
        "talk_travel":    ("✈️ Путешествия",         "✈️ Travel"),
        "talk_work":      ("💼 Работа",             "💼 Work"),
        "talk_debate":    ("⚔️ Дебаты",             "⚔️ Debate"),
        "talk_business":  ("🤝 Бизнес English",     "🤝 Business English"),
        "talk_free":      ("💭 Свободная беседа",   "💭 Free Chat"),
        "talk_interview": ("👔 Mock Interview",     "👔 Mock Interview"),
    }
    rows = [[InlineKeyboardButton(text=v[0 if lang=="ru" else 1], callback_data=k)] for k,v in labels.items()]
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def remind_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌅 08:00", callback_data="remind_08:00"),
         InlineKeyboardButton(text="☀️ 10:00", callback_data="remind_10:00")],
        [InlineKeyboardButton(text="🌞 12:00", callback_data="remind_12:00"),
         InlineKeyboardButton(text="🌇 18:00", callback_data="remind_18:00")],
        [InlineKeyboardButton(text="🌆 19:00", callback_data="remind_19:00"),
         InlineKeyboardButton(text="🌙 21:00", callback_data="remind_21:00")],
        [InlineKeyboardButton(text="❌ Disable", callback_data="remind_off")],
    ])

def flashcard_kb(word_id: int, lang: str):
    if lang == "ru":
        opts = [("😕 Не знал",1),("🤔 Почти",2),("😊 Помнил",4),("✅ Легко",5)]
    else:
        opts = [("😕 Forgot",1),("🤔 Hard",2),("😊 Good",4),("✅ Easy",5)]
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=l, callback_data=f"fc_{word_id}_{q}") for l,q in opts
    ]])

def shadowing_kb(lang: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🔊 Ещё раз" if lang=="ru" else "🔊 Hear again",
            callback_data="shadow_repeat"
        ),
        InlineKeyboardButton(
            text="✍️ Написать" if lang=="ru" else "✍️ Write it",
            callback_data="shadow_write"
        ),
    ]])


# ══════════════════════════════════════════════════════════════════
#  ПЛАНИРОВЩИК
# ══════════════════════════════════════════════════════════════════

async def send_reminder(uid: int):
    user = get_user(uid)
    if not user: return
    lang     = user.get("lang","ru")
    streak   = get_streak_count(uid)
    due_cnt  = len(get_due_words(uid, limit=10))
    interests = get_interests(uid)

    # Персонализированное приветствие с контекстом интересов
    interest_hint = ""
    if interests:
        first = interests.split(",")[0].strip()
        hints_ru = [f"Сегодня разберём слова из мира {first}?", f"Практикуем English на теме {first}!"]
        hints_en = [f"Today let's practice English around {first}!", f"New words from the world of {first} await!"]
        interest_hint = "\n" + random.choice(hints_ru if lang=="ru" else hints_en)

    msgs_ru = [
        f"📚 <b>Время английского!</b>{interest_hint}",
        f"🔥 <b>Не теряй прогресс!</b> ALEX ждёт.{interest_hint}",
        f"⚡️ <b>Ежедневная практика = беглый English.</b>{interest_hint}",
    ]
    msgs_en = [
        f"📚 <b>Time to practice!</b>{interest_hint}",
        f"🔥 <b>Keep your streak alive!</b>{interest_hint}",
        f"⚡️ <b>Daily practice = fluency.</b>{interest_hint}",
    ]
    text = random.choice(msgs_ru if lang=="ru" else msgs_en)
    if streak > 2:
        text += f"\n\n🔥 Streak: <b>{streak} {'дней' if lang=='ru' else 'days'}</b>!"
    if due_cnt > 0:
        text += f"\n📅 <b>{due_cnt}</b> {'слов ждут повторения' if lang=='ru' else 'words due for review'} → /vocab"
    try:
        await bot.send_message(uid, text)
    except Exception as e:
        logger.warning(f"Reminder failed {uid}: {e}")

async def send_weekly_report(uid: int):
    stats = get_full_stats(uid)
    lang  = get_lang(uid)
    try:
        await bot.send_message(uid,
            f"📊 <b>{'Еженедельный отчёт' if lang=='ru' else 'Weekly Report'}</b>\n\n"
            f"🎯 {stats['level']} · {stats['rank']} · ⭐ {stats['xp']} XP\n"
            f"🔥 Streak: <b>{stats['streak']}</b> · 📅 Sessions: <b>{stats['sessions']}</b>\n"
            f"📝 Words: <b>{stats['words']}</b> · ✅ Tests: <b>{stats['tests']}</b>"
        )
    except Exception: pass

def schedule_all():
    rows = db("SELECT uid, remind_time FROM users WHERE remind_time IS NOT NULL AND remind_time != 'off'", fetch=True)
    if rows:
        for r in rows:
            try:
                h, m = map(int, r["remind_time"].split(":"))
                scheduler.add_job(send_reminder,"cron",hour=h,minute=m,args=[r["uid"]],id=f"remind_{r['uid']}",replace_existing=True)
            except Exception: pass
    all_users = db("SELECT uid FROM users", fetch=True)
    if all_users:
        for r in all_users:
            scheduler.add_job(send_weekly_report,"cron",day_of_week="sun",hour=19,args=[r["uid"]],id=f"weekly_{r['uid']}",replace_existing=True)


# ══════════════════════════════════════════════════════════════════
#  INLINE MODE — перевод в любом чате
# ══════════════════════════════════════════════════════════════════

@dp.inline_query()
async def handle_inline(query: InlineQuery):
    text = query.query.strip()
    if not text or len(text) < 2:
        await query.answer([], cache_time=1)
        return

    uid  = query.from_user.id
    lang = get_lang(uid) if get_user(uid) else "ru"

    # Быстрый перевод через ALEX
    system = (
        "You are a compact translation assistant. Given any text, return ONLY a JSON object:\n"
        '{"translation": "...", "explanation": "...", "example": "..."}\n'
        "translation = the translation to Russian if input is English, or to English if input is Russian\n"
        "explanation = brief grammar note or usage tip (1 sentence)\n"
        "example = one natural example sentence using the word/phrase\n"
        "NO other text, just JSON."
    )
    raw = await ask_alex_raw(text, system)
    try:
        data = json.loads(re.search(r'\{.*\}', raw, re.DOTALL).group())
        translation = data.get("translation","")
        explanation = data.get("explanation","")
        example     = data.get("example","")
    except Exception:
        translation = raw[:100] if raw else "Translation error"
        explanation = ""
        example     = ""

    results = [
        InlineQueryResultArticle(
            id="1",
            title=f"🔤 {translation}",
            description=explanation[:100] if explanation else "",
            input_message_content=InputTextMessageContent(
                message_text=(
                    f"<b>{text}</b> → {translation}\n"
                    + (f"<i>{explanation}</i>\n" if explanation else "")
                    + (f"📝 {example}" if example else "")
                ),
                parse_mode="HTML"
            ),
        ),
        InlineQueryResultArticle(
            id="2",
            title="📝 Example sentence",
            description=example[:100] if example else "No example",
            input_message_content=InputTextMessageContent(
                message_text=f"<b>Example:</b> <i>{example}</i>",
                parse_mode="HTML"
            ),
        ),
    ]
    await query.answer(results, cache_time=30)


# ══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ══════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid  = message.from_user.id
    name = message.from_user.first_name or "Student"
    upsert_user(uid, name)
    scheduler.add_job(send_weekly_report,"cron",day_of_week="sun",hour=19,args=[uid],id=f"weekly_{uid}",replace_existing=True)

    user = get_user(uid)
    if not user or not user.get("lang"):
        await message.answer("🌍 Choose language / Выбери язык:", reply_markup=lang_kb())
        return

    lang = get_lang(uid)
    interests = get_interests(uid)

    # Персонализированное приветствие
    if interests:
        first = interests.split(",")[0].strip()
        ctx_msg = f"\n\n💡 {'Сегодня можем поработать над темой' if lang=='ru' else 'Today we can work on'} <b>{first}</b>!"
    else:
        ctx_msg = ""

    welcome = (
        f"<b>{'Привет' if lang=='ru' else 'Hey'}, {name}!</b> 👋\n\n"
        f"Я <b>ALEX</b> — {'твой AI-репетитор английского.' if lang=='ru' else 'your AI English tutor.'}"
        f"{ctx_msg}\n\n"
        f"<i>{'Выбери с чего начать 👇' if lang=='ru' else 'Choose where to start 👇'}</i>"
    )
    await message.answer(welcome, reply_markup=main_kb(lang))
    await message.answer("🎯 <b>Level:</b>", reply_markup=level_kb())


@dp.message(Command("lang"))
async def cmd_lang(message: Message):
    await message.answer("🌍 Choose / Выбери:", reply_markup=lang_kb())

@dp.message(Command("level"))
async def cmd_level(message: Message):
    await message.answer("🎯 Choose your level:", reply_markup=level_kb())

@dp.message(Command("help"))
async def cmd_help(message: Message):
    lang = get_lang(message.from_user.id)
    text = (
        "<b>LinguaMax ALEX v3</b>\n\n"
        "/lesson — уроки грамматики\n"
        "/vocab — словарный тренажёр\n"
        "/roleplay — ролевые диалоги\n"
        "/story — Story Quest RPG\n"
        "/debate — дебаты (3 раунда)\n"
        "/test — тесты\n"
        "/toefl — подготовка к TOEFL\n"
        "/shadow — shadowing фраза\n"
        "/writing — проверка текста\n"
        "/sentence — конструктор предложений\n"
        "/idioms — идиомы\n"
        "/talk — разговорная практика\n"
        "/stats — прогресс и XP\n"
        "/mistakes — мои ошибки\n"
        "/interests — мои интересы\n"
        "/remind — напоминания\n"
        "/reset — сброс диалога\n\n"
        "📸 Фото → анализ текста\n"
        "✍️ Пиши по-английски → автокоррекция\n"
        "🔤 Инлайн: @бот слово → перевод в любом чате"
        if lang == "ru" else
        "<b>LinguaMax ALEX v3</b>\n\n"
        "/lesson — grammar lessons\n"
        "/vocab — vocabulary trainer\n"
        "/roleplay — roleplay scenarios\n"
        "/story — Story Quest RPG\n"
        "/debate — structured debate\n"
        "/test — tests\n"
        "/toefl — TOEFL preparation\n"
        "/shadow — shadowing phrase\n"
        "/writing — text check\n"
        "/sentence — sentence builder\n"
        "/idioms — idioms\n"
        "/talk — speaking practice\n"
        "/stats — progress & XP\n"
        "/mistakes — my errors\n"
        "/interests — my interests\n"
        "/remind — reminders\n"
        "/reset — clear dialogue\n\n"
        "📸 Photo → text analysis\n"
        "✍️ Write in English → auto-correction\n"
        "🔤 Inline: @bot word → translate in any chat"
    )
    await message.answer(text)

@dp.message(Command("lesson"))
async def cmd_lesson(m: Message):
    lang = get_lang(m.from_user.id)
    await m.answer("📚 <b>Grammar Lessons:</b>", reply_markup=lesson_kb(lang))

@dp.message(Command("vocab"))
async def cmd_vocab(m: Message):
    lang = get_lang(m.from_user.id)
    await m.answer("📝 <b>Vocabulary:</b>", reply_markup=vocab_kb(lang))

@dp.message(Command("roleplay"))
async def cmd_roleplay(m: Message):
    lang = get_lang(m.from_user.id)
    await m.answer("🎭 <b>Roleplay:</b>", reply_markup=roleplay_kb(lang))

@dp.message(Command("story"))
async def cmd_story(m: Message):
    lang = get_lang(m.from_user.id)
    title = "🎮 <b>Story Quest — выбери жанр:</b>" if lang=="ru" else "🎮 <b>Story Quest — choose genre:</b>"
    await m.answer(title, reply_markup=story_kb(lang))

@dp.message(Command("debate"))
async def cmd_debate(m: Message):
    uid  = m.from_user.id
    lang = get_lang(uid)
    clear_history(uid)
    set_ctx(uid, debate_round=1)
    await bot.send_chat_action(m.chat.id, "typing")
    reply = await ask_alex(uid,
        "Start a debate exercise. Choose an interesting controversial topic (AI, social media, remote work, etc.). "
        "Assign me a position (FOR or AGAINST). Explain the debate rules briefly, then begin Round 1 by stating my position's task.",
        mode="debate"
    )
    await m.answer(reply)
    log_session(uid, "debate")
    waiting[uid] = "debate_active"

@dp.message(Command("test"))
async def cmd_test(m: Message):
    lang = get_lang(m.from_user.id)
    await m.answer("✅ <b>Tests:</b>", reply_markup=test_kb(lang))

@dp.message(Command("toefl"))
async def cmd_toefl(m: Message):
    lang = get_lang(m.from_user.id)
    await m.answer("🎓 <b>TOEFL iBT:</b>", reply_markup=toefl_kb(lang))

@dp.message(Command("talk"))
async def cmd_talk(m: Message):
    lang = get_lang(m.from_user.id)
    await m.answer("💬 <b>Speaking:</b>", reply_markup=talk_kb(lang))

@dp.message(Command("shadow"))
async def cmd_shadow(m: Message):
    uid   = m.from_user.id
    lang  = get_lang(uid)
    level = get_level(uid)
    await bot.send_chat_action(m.chat.id, "typing")

    # Получаем фразу для shadowing
    phrase_raw = await ask_alex_raw(
        f"Give me ONE shadowing practice phrase for level {level}. "
        "It should be a complete, natural English sentence (10-20 words). "
        "Return ONLY the phrase, nothing else.",
        "You are a pronunciation coach. Return only the practice phrase."
    )
    phrase = phrase_raw.strip().strip('"').strip("'")
    set_ctx(uid, shadow_phrase=phrase)

    # TTS
    audio = await text_to_speech(phrase)
    if audio:
        await m.answer_voice(
            BufferedInputFile(audio, filename="phrase.mp3"),
            caption=f"🎙 <b>Shadowing</b>\n\n<i>{phrase}</i>\n\n{'Повтори эту фразу письменно 👇' if lang=='ru' else 'Write this phrase below 👇'}",
            reply_markup=shadowing_kb(lang)
        )
    else:
        await m.answer(
            f"🎙 <b>Shadowing</b>\n\n<i>{phrase}</i>\n\n{'Напиши эту фразу точно:' if lang=='ru' else 'Write this phrase exactly:'}",
            reply_markup=shadowing_kb(lang)
        )
    waiting[uid] = "shadowing"
    log_session(uid, "shadowing")

@dp.message(Command("writing"))
async def cmd_writing(m: Message):
    uid  = m.from_user.id
    lang = get_lang(uid)
    await m.answer(
        "✍️ <b>{'Проверка текста' if lang=='ru' else 'Writing Check'}</b>\n\n"
        "{'Отправь текст — получишь 3 версии:' if lang=='ru' else 'Send text — get 3 versions:'}\n"
        "✅ Corrected · 🌟 Native-like · 📚 Error breakdown"
    )
    waiting[uid] = "writing"

@dp.message(Command("sentence"))
async def cmd_sentence(m: Message):
    uid   = m.from_user.id
    level = get_level(uid)
    await bot.send_chat_action(m.chat.id, "typing")
    reply = await ask_alex(uid,
        f"Give me a sentence builder exercise for {level} level. "
        "Provide 6-8 jumbled words, ask me to arrange them. "
        "After I answer, confirm correct or show the right answer with explanation.",
        mode="grammar"
    )
    await m.answer(reply)
    log_session(uid, "sentence_builder")
    waiting[uid] = "lesson_active"

@dp.message(Command("idioms"))
async def cmd_idioms(m: Message):
    uid  = m.from_user.id
    level = get_level(uid)
    await bot.send_chat_action(m.chat.id, "typing")
    reply = await ask_alex(uid,
        f"Teach me 5 English idioms for {level} level. "
        "Each: idiom, meaning, brief origin, 2 natural examples, when to use it. Make it memorable.",
        mode="vocab"
    )
    await m.answer(reply)
    log_session(uid, "idioms")

@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    uid   = m.from_user.id
    lang  = get_lang(uid)
    stats = get_full_stats(uid)
    xp    = stats["xp"]
    interests = get_all_interests(uid)
    interest_line = ", ".join(r["interest"] for r in interests[:5]) if interests else ("нет" if lang=="ru" else "none")

    nxt_xp = {"🌱 Seedling":100,"📗 Beginner":300,"📘 Elementary":600,
               "📙 Pre-Intermediate":1000,"⭐ Intermediate":1500,
               "🌟 Upper-Intermediate":2500,"💫 Advanced":4000,"🏆 Master":9999}
    nxt = nxt_xp.get(stats["rank"],9999)
    bar = "█"*min(10,int(xp/nxt*10)) + "░"*max(0,10-int(xp/nxt*10))

    await m.answer(
        f"📊 <b>{'Прогресс' if lang=='ru' else 'Progress'}</b>\n\n"
        f"🎯 {stats['level']} · {stats['rank']}\n"
        f"⭐ XP: <b>{xp}</b> [{bar}]\n\n"
        f"🔥 Streak: <b>{stats['streak']} {'дней' if lang=='ru' else 'days'}</b>\n"
        f"📅 Sessions: <b>{stats['sessions']}</b> · ✅ Tests: <b>{stats['tests']}</b>\n"
        f"📝 Words: <b>{stats['words']}</b> · ❌ Errors: <b>{stats['errors']}</b>\n"
        f"🎓 TOEFL: <b>{stats['toefl']}</b>\n\n"
        f"🎮 Interests: <i>{interest_line}</i>"
    )

@dp.message(Command("mistakes"))
async def cmd_mistakes(m: Message):
    uid  = m.from_user.id
    lang = get_lang(uid)
    rows = get_mistakes(uid, limit=10)
    if not rows:
        await m.answer("✅ No mistakes yet! Write something in English." if lang=="en" else "✅ Ошибок пока нет!")
        return
    text = "❌ <b>Recent mistakes:</b>\n\n"
    for i,r in enumerate(rows,1):
        text += f"{i}. ❌ <code>{r['original'][:50]}</code>\n   ✅ <i>{r['corrected'][:50]}</i>\n   💡 {r['explanation'][:80]}\n\n"
    await m.answer(text)

@dp.message(Command("interests"))
async def cmd_interests(m: Message):
    uid  = m.from_user.id
    lang = get_lang(uid)
    current = get_all_interests(uid)
    current_list = ", ".join(r["interest"] for r in current) if current else ("пусто" if lang=="ru" else "empty")
    await m.answer(
        f"🎮 <b>{'Твои интересы:' if lang=='ru' else 'Your interests:'}</b> <i>{current_list}</i>\n\n"
        f"{'Напиши новые через запятую (или просто общайся — ALEX запомнит сам):' if lang=='ru' else 'Write new interests comma-separated (or just chat — ALEX auto-saves them):'}\n"
        f"<code>gaming, music, travel, tech</code>"
    )
    waiting[uid] = "set_interests"

@dp.message(Command("remind"))
async def cmd_remind(m: Message):
    await m.answer("⏰ Set daily reminder:", reply_markup=remind_kb())

@dp.message(Command("reset"))
async def cmd_reset(m: Message):
    uid = m.from_user.id
    clear_history(uid)
    waiting.pop(uid, None)
    clear_ctx(uid)
    lang = get_lang(uid)
    await m.answer("🔄 <b>Reset.</b>" if lang=="en" else "🔄 <b>Диалог сброшен.</b>")


# ══════════════════════════════════════════════════════════════════
#  CALLBACK
# ══════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = cb.data.replace("lang_","")
    update_user(uid, lang=lang)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅")
    name = cb.from_user.first_name or "Student"
    await cb.message.answer(
        f"<b>{'Привет' if lang=='ru' else 'Hey'}, {name}!</b> 👋\n\n"
        f"Я <b>ALEX</b>. {'Выбери уровень:' if lang=='ru' else 'Choose your level:'}",
        reply_markup=main_kb(lang)
    )
    await cb.message.answer("🎯 <b>Level:</b>", reply_markup=level_kb())

@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Menu 👇", reply_markup=main_kb(lang))
    await cb.answer()

@dp.callback_query(F.data.startswith("setlevel_"))
async def cb_setlevel(cb: CallbackQuery):
    uid   = cb.from_user.id
    lang  = get_lang(uid)
    level = cb.data.replace("setlevel_","")
    update_user(uid, level=level)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(f"✅ {level}")
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid,
        f"My English level is {level}. Give me a brief encouraging welcome, tell me what we'll focus on, and suggest the best thing to start with today. Be specific.",
        mode="general"
    )
    await cb.message.answer(reply)
    log_session(uid, "level_set")

@dp.callback_query(F.data.startswith("rp_"))
async def cb_roleplay(cb: CallbackQuery):
    uid      = cb.from_user.id
    lang     = get_lang(uid)
    scenario = ROLEPLAY_SCENARIOS.get(cb.data)
    if not scenario: await cb.answer(); return
    if cb.data == "rp_custom":
        await cb.answer()
        await cb.message.answer("🎭 Опиши ситуацию:" if lang=="ru" else "🎭 Describe the scenario:")
        waiting[uid] = "rp_custom"
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
    uid        = cb.from_user.id
    story_data = STORY_TYPES.get(cb.data)
    if not story_data: await cb.answer(); return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    clear_history(uid)
    start_story(uid, cb.data)
    set_ctx(uid, story_type=cb.data, story_hp=100, story_score=0)
    await bot.send_chat_action(cb.message.chat.id, "typing")
    extra = f"story_type: {cb.data}, chapter: 1"
    reply = await ask_alex(uid, story_data["prompt"], mode="story", extra=extra)
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

@dp.callback_query(F.data.startswith("vocab_") | F.data.in_(["daily_quiz"]))
async def cb_vocab(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)
    if cb.data in ("vocab_review","daily_quiz"):
        due = get_due_words(uid, limit=5)
        if not due:
            await cb.answer()
            await cb.message.answer("✅ No words due today!" if lang=="en" else "✅ Сегодня нет слов для повторения!")
            return
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer()
        word = dict(due[0])
        set_ctx(uid, review_queue=[dict(w) for w in due], review_idx=0)
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
    lang    = get_lang(uid)
    parts   = cb.data.split("_")
    word_id = int(parts[1]); quality = int(parts[2])
    update_word_review(word_id, quality)
    add_xp(uid, 3)
    ctx = get_ctx(uid)
    queue = ctx.get("review_queue",[])
    idx   = ctx.get("review_idx",0) + 1
    set_ctx(uid, review_idx=idx)
    await cb.message.edit_reply_markup(reply_markup=None)
    word_data = db("SELECT * FROM vocabulary WHERE id=?", (word_id,), fetch=True)
    if word_data:
        w = dict(word_data[0])
        await cb.message.answer(f"✅ <b>{w['word']}</b> = {w['translation']}\n<i>{w['example']}</i>")
    if idx < len(queue):
        word = queue[idx]
        await cb.message.answer(
            f"🃏 <b>Card {idx+1}/{len(queue)}</b>\n\n📖 <b>{word['word']}</b>\n\n<i>{word['example']}</i>",
            reply_markup=flashcard_kb(word["id"], lang)
        )
    else:
        await cb.message.answer(f"✅ <b>Done!</b> +{len(queue)*3} XP 🎉")
        log_session(uid, "vocab_review", score=len(queue), total=len(queue))
        clear_ctx(uid)
    await cb.answer()

# TOEFL callbacks
@dp.callback_query(F.data.startswith("toefl_"))
async def cb_toefl(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)

    if cb.data == "toefl_score":
        await cb.answer()
        rows = get_toefl_scores(uid)
        if not rows:
            await cb.message.answer("🎓 No scores yet. Start practicing!")
            return
        text = "🎓 <b>TOEFL Scores:</b>\n\n"
        for r in rows:
            text += f"📌 <b>{r['section']}</b>: best {r['best']}, avg {r['avg_s']:.0f} ({r['cnt']} sessions)\n"
        await cb.message.answer(text)
        return

    if cb.data == "toefl_listening":
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer()
        await run_toefl_listening(uid, cb.message)
        return

    all_prompts = {**TOEFL_PROMPTS}
    prompt = all_prompts.get(cb.data,"")
    if not prompt: await cb.answer(); return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="toefl")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "toefl_active"

@dp.callback_query(F.data == "toefl_q_start")
async def cb_toefl_q_start(cb: CallbackQuery):
    uid = cb.from_user.id
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await send_toefl_question(uid, cb.message, 0)
    set_ctx(uid, toefl_q_idx=0)
    waiting[uid] = "toefl_listening_answers"

@dp.callback_query(F.data.startswith("toefl_ans_"))
async def cb_toefl_answer(cb: CallbackQuery):
    uid   = cb.from_user.id
    parts = cb.data.split("_")
    q_idx = int(parts[2]); answer = parts[3]
    ctx   = get_ctx(uid)
    answers = ctx.get("toefl_answers", {})
    answers[str(q_idx)] = answer
    set_ctx(uid, toefl_answers=answers)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer(f"✅ {answer}")
    next_idx = q_idx + 1
    questions = ctx.get("toefl_questions", [])
    if next_idx < len(questions):
        await send_toefl_question(uid, cb.message, next_idx)
        set_ctx(uid, toefl_q_idx=next_idx)
    else:
        await finish_toefl_listening(uid, cb.message)

# Test / talk callbacks
@dp.callback_query(F.data.startswith(("test_","talk_")))
async def cb_test_talk(cb: CallbackQuery):
    uid  = cb.from_user.id
    all_p = {**TEST_PROMPTS, **TALK_PROMPTS}
    mode  = "test" if cb.data.startswith("test_") else "speaking"
    prompt = all_p.get(cb.data,"")
    if not prompt: await cb.answer(); return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode=mode)
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "test_active" if mode=="test" else "speaking_active"

# Shadowing callbacks
@dp.callback_query(F.data == "shadow_repeat")
async def cb_shadow_repeat(cb: CallbackQuery):
    uid    = cb.from_user.id
    lang   = get_lang(uid)
    phrase = get_ctx(uid).get("shadow_phrase","")
    if phrase:
        audio = await text_to_speech(phrase)
        if audio:
            await cb.message.answer_voice(BufferedInputFile(audio,"phrase.mp3"), caption=f"🔊 <i>{phrase}</i>")
        else:
            await cb.message.answer(f"🔊 <i>{phrase}</i>")
    await cb.answer()

@dp.callback_query(F.data == "shadow_write")
async def cb_shadow_write(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)
    await cb.answer()
    await cb.message.answer("✍️ Write the phrase:" if lang=="en" else "✍️ Напиши фразу:")
    waiting[uid] = "shadowing"

# Remind callbacks
@dp.callback_query(F.data.startswith("remind_"))
async def cb_remind(cb: CallbackQuery):
    uid  = cb.from_user.id
    data = cb.data.replace("remind_","")
    if data == "off":
        update_user(uid, remind_time="off")
        if scheduler.get_job(f"remind_{uid}"): scheduler.remove_job(f"remind_{uid}")
        await cb.answer()
        await cb.message.edit_text("❌ Reminders disabled.")
    else:
        update_user(uid, remind_time=data)
        try:
            h, m = map(int, data.split(":"))
            scheduler.add_job(send_reminder,"cron",hour=h,minute=m,args=[uid],id=f"remind_{uid}",replace_existing=True)
        except Exception as e: logger.warning(e)
        await cb.answer(f"✅ {data}")
        await cb.message.edit_text(f"✅ <b>Reminder: {data}</b> every day 📚")


# ══════════════════════════════════════════════════════════════════
#  ФОТО
# ══════════════════════════════════════════════════════════════════

@dp.message(F.photo)
async def handle_photo(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    photo: PhotoSize = message.photo[-1]
    fi = await bot.get_file(photo.file_id)
    fb = await bot.download_file(fi.file_path)
    pb = fb.read() if hasattr(fb,"read") else bytes(fb)
    await message.answer("📸 Analyzing..." if lang=="en" else "📸 Анализирую...")
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await analyze_photo(uid, pb)
    await message.answer(reply)
    log_session(uid, "photo_analysis")


# ══════════════════════════════════════════════════════════════════
#  ТЕКСТ — главный обработчик
# ══════════════════════════════════════════════════════════════════

MENU_RU = {
    "📚 Урок грамматики": "lesson", "📝 Словарь": "vocab",
    "🎭 Ролевой диалог": "roleplay", "🎮 Story Quest": "story",
    "✅ Тест": "test", "🎓 TOEFL": "toefl",
    "✍️ Проверить текст": "writing", "💬 Разговор": "talk",
    "⚔️ Дебаты": "debate", "🗣 Идиомы": "idioms",
    "❌ Мои ошибки": "mistakes", "📊 Прогресс": "stats",
}
MENU_EN = {
    "📚 Grammar Lesson": "lesson", "📝 Vocabulary": "vocab",
    "🎭 Roleplay": "roleplay", "🎮 Story Quest": "story",
    "✅ Test": "test", "🎓 TOEFL": "toefl",
    "✍️ Check Writing": "writing", "💬 Speaking": "talk",
    "⚔️ Debate": "debate", "🗣 Idioms": "idioms",
    "❌ My Mistakes": "mistakes", "📊 Progress": "stats",
}

@dp.message(F.text)
async def handle_text(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    text  = message.text.strip()
    state = waiting.get(uid,"")

    # ── Кнопки меню ───────────────────────────────────────────────
    menu   = MENU_RU if lang=="ru" else MENU_EN
    action = menu.get(text)
    if action:
        handlers = {
            "lesson": cmd_lesson, "vocab": cmd_vocab, "roleplay": cmd_roleplay,
            "story": cmd_story, "test": cmd_test, "toefl": cmd_toefl,
            "writing": cmd_writing, "talk": cmd_talk, "debate": cmd_debate,
            "idioms": cmd_idioms, "mistakes": cmd_mistakes, "stats": cmd_stats,
        }
        h = handlers.get(action)
        if h: await h(message)
        return

    # ── Состояния ─────────────────────────────────────────────────
    if state == "set_interests":
        waiting.pop(uid, None)
        for interest in [i.strip() for i in text.split(",") if i.strip()]:
            save_interest(uid, interest, source="manual")
        update_user(uid, interests=text[:200])
        await message.answer(
            f"✅ {'Запомнил! Буду строить примеры на основе твоих интересов.' if lang=='ru' else 'Saved! Examples will be based on your interests.'}\n<i>{text}</i>"
        )
        return

    if state == "rp_custom":
        waiting.pop(uid, None)
        clear_history(uid)
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, f"Start roleplay: {text}. Begin immediately in character.", mode="roleplay")
        await message.answer(reply)
        log_session(uid, "roleplay_custom")
        waiting[uid] = "roleplay_active"
        return

    if state == "writing":
        waiting.pop(uid, None)
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, f"3-layer analysis of this text:\n\n{text}", mode="correction")
        await message.answer(reply)
        log_session(uid, "writing_check")
        if len(text) > 20:
            log_mistake(uid, text[:100], "See correction", "writing", "mixed")
        return

    if state == "shadowing":
        phrase = get_ctx(uid).get("shadow_phrase","")
        if phrase:
            # Сравниваем текст пользователя с фразой
            match = sum(1 for a,b in zip(text.lower().split(), phrase.lower().split()) if a==b)
            total = len(phrase.split())
            pct   = int(match/total*100) if total else 0
            if pct >= 90:
                msg = f"🎉 <b>{'Отлично!' if lang=='ru' else 'Excellent!'}</b> {pct}% match!\n<i>{phrase}</i>"
            elif pct >= 70:
                msg = f"👍 <b>{'Почти!' if lang=='ru' else 'Almost!'}</b> {pct}%\n✅ <i>{phrase}</i>"
            else:
                msg = f"💪 <b>{pct}%</b> — {'попробуй ещё раз:' if lang=='ru' else 'try again:'}\n<i>{phrase}</i>"
            await message.answer(msg, reply_markup=shadowing_kb(lang))
            add_xp(uid, 10)
        waiting.pop(uid, None)
        clear_ctx(uid)
        return

    if state == "story_active":
        ctx  = get_ctx(uid)
        hp   = ctx.get("story_hp",100)
        score = ctx.get("story_score",0)
        en_ratio = sum(1 for c in text if c.isalpha() and ord(c)<128)/max(len(text),1)
        extra = f"Current HP: {hp}/100, Score: {score}, Chapter: {ctx.get('story_chapter',1)}/5"
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode="story", extra=extra)
        # Обновляем HP если есть ошибки (ALEX сам управляет в ответе)
        if "HP:" in reply:
            try:
                new_hp = int(re.search(r'HP:\s*(\d+)', reply).group(1))
                set_ctx(uid, story_hp=new_hp)
                update_story(uid, hp=new_hp)
            except Exception: pass
        await message.answer(reply)
        return

    if state in ("test_active","lesson_active","speaking_active","toefl_active","vocab_active","debate_active","roleplay_active"):
        mode_map = {
            "test_active":"test","lesson_active":"grammar","speaking_active":"speaking",
            "toefl_active":"toefl","vocab_active":"vocab","debate_active":"debate","roleplay_active":"roleplay",
        }
        mode = mode_map.get(state,"general")
        lower = text.lower()
        if any(p in lower for p in ["объясни иначе","explain differently","i don't get it","другой пример","new analogy"]):
            await bot.send_chat_action(message.chat.id, "typing")
            reply = await ask_alex(uid, "Explain the same thing using a completely different analogy or approach. Be creative.", mode=mode)
            await message.answer(reply)
            return
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode=mode)
        await message.answer(reply)
        return

    # ── Автокоррекция английского ─────────────────────────────────
    en_ratio = sum(1 for c in text if c.isalpha() and ord(c)<128)/max(len(text),1)
    if en_ratio > 0.6 and len(text) > 8:
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode="correction",
            extra="If errors exist: correct them. If perfect: compliment + expand the conversation naturally."
        )
        await message.answer(reply)
        log_session(uid, "free_writing")
        if len(text) > 15:
            log_mistake(uid, text[:100], "See correction", "free writing", "mixed")
    else:
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode="general")
        await message.answer(reply)
        log_session(uid, "chat")


# ══════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════

async def main():
    db_init()
    schedule_all()
    scheduler.start()
    logger.info("🎓 LinguaMax ALEX v3 Ultimate — запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
