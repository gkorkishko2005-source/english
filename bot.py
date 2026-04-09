"""
╔══════════════════════════════════════════════════════════════════╗
   LinguaMax · Репетитор английского ALEX
   aiogram 3.x · Anthropic Claude Haiku · SQLite · APScheduler
   
   ФУНКЦИИ:
   ✅ Уроки грамматики (8 тем)
   ✅ Словарный тренажёр + флэш-карточки
   ✅ Разговорная практика (7 тем)
   ✅ Диктант
   ✅ Проверка и исправление текста
   ✅ Идиомы и сленг
   ✅ Тесты по грамматике, лексике, чтению
   ✅ Определение уровня (A1-C2)
   ✅ Подготовка к TOEFL (все 4 секции)
   ✅ База ошибок пользователя
   ✅ Стрик занятий
   ✅ Анализ фото с текстом
   ✅ Еженедельный отчёт
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import base64
import logging
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message,
    PhotoSize, ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from translations import T, t

load_dotenv()
BOT_TOKEN     = os.getenv("BOT_TOKEN")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL         = "claude-haiku-4-5"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

bot       = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp        = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

# ══════════════════════════════════════════════════════════════════
#  БАЗА ДАННЫХ
# ══════════════════════════════════════════════════════════════════

DB = "linguamax.db"

def db_init():
    con = sqlite3.connect(DB)
    c   = con.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        uid          INTEGER PRIMARY KEY,
        name         TEXT,
        lang         TEXT DEFAULT 'ru',
        level        TEXT DEFAULT 'B1',
        remind_time  TEXT,
        created_at   TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER,
        type       TEXT,
        date       TEXT,
        score      INTEGER DEFAULT 0,
        total      INTEGER DEFAULT 0,
        notes      TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS vocabulary (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        uid          INTEGER,
        word         TEXT,
        translation  TEXT,
        example      TEXT,
        topic        TEXT,
        learned      INTEGER DEFAULT 0,
        review_count INTEGER DEFAULT 0,
        created_at   TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS mistakes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        uid         INTEGER,
        original    TEXT,
        corrected   TEXT,
        explanation TEXT,
        category    TEXT,
        date        TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS toefl_scores (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        uid      INTEGER,
        section  TEXT,
        score    INTEGER,
        max_score INTEGER,
        date     TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")

    con.commit()
    con.close()


def db(query: str, params=(), fetch=False):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    c = con.cursor()
    c.execute(query, params)
    result = c.fetchall() if fetch else None
    con.commit()
    con.close()
    return result


def get_user(uid: int):
    rows = db("SELECT * FROM users WHERE uid=?", (uid,), fetch=True)
    return dict(rows[0]) if rows else None

def get_lang(uid: int) -> str:
    u = get_user(uid)
    return (u.get("lang") or "ru") if u else "ru"

def get_level(uid: int) -> str:
    u = get_user(uid)
    return (u.get("level") or "B1") if u else "B1"

def upsert_user(uid: int, name: str):
    db("INSERT OR IGNORE INTO users (uid, name) VALUES (?,?)", (uid, name))

def update_user(uid: int, **kwargs):
    for k, v in kwargs.items():
        db(f"UPDATE users SET {k}=? WHERE uid=?", (v, uid))

def log_session(uid: int, stype: str, score: int = 0, total: int = 0, notes: str = ""):
    db("INSERT INTO sessions (uid,type,date,score,total,notes) VALUES (?,?,?,?,?,?)",
       (uid, stype, datetime.now().strftime("%Y-%m-%d"), score, total, notes))

def log_mistake(uid: int, original: str, corrected: str, explanation: str, category: str = "grammar"):
    db("INSERT INTO mistakes (uid,original,corrected,explanation,category,date) VALUES (?,?,?,?,?,?)",
       (uid, original[:200], corrected[:200], explanation[:500], category, datetime.now().strftime("%Y-%m-%d")))

def log_toefl(uid: int, section: str, score: int, max_score: int):
    db("INSERT INTO toefl_scores (uid,section,score,max_score,date) VALUES (?,?,?,?,?)",
       (uid, section, score, max_score, datetime.now().strftime("%Y-%m-%d")))

def add_word(uid: int, word: str, translation: str, example: str, topic: str = "general"):
    exists = db("SELECT id FROM vocabulary WHERE uid=? AND word=?", (uid, word.lower()), fetch=True)
    if not exists:
        db("INSERT INTO vocabulary (uid,word,translation,example,topic) VALUES (?,?,?,?,?)",
           (uid, word.lower(), translation, example, topic))

def get_stats(uid: int) -> dict:
    week = (datetime.now()-timedelta(days=7)).strftime("%Y-%m-%d")
    total_s  = db("SELECT COUNT(*) as c FROM sessions WHERE uid=?", (uid,), fetch=True)[0]["c"]
    total_t  = db("SELECT COUNT(*) as c FROM sessions WHERE uid=? AND type LIKE '%test%'", (uid,), fetch=True)[0]["c"]
    total_w  = db("SELECT COUNT(*) as c FROM vocabulary WHERE uid=? AND learned=1", (uid,), fetch=True)[0]["c"]
    total_e  = db("SELECT COUNT(*) as c FROM mistakes WHERE uid=?", (uid,), fetch=True)[0]["c"]
    toefl_c  = db("SELECT COUNT(*) as c FROM sessions WHERE uid=? AND type LIKE '%toefl%'", (uid,), fetch=True)[0]["c"]
    streak   = get_streak(uid)
    return {"sessions": total_s, "tests": total_t, "words": total_w,
            "errors": total_e, "toefl": toefl_c, "streak": streak}

def get_streak(uid: int) -> int:
    rows = db("SELECT DISTINCT date FROM sessions WHERE uid=? ORDER BY date DESC", (uid,), fetch=True)
    if not rows: return 0
    streak = 0; current = datetime.now().date()
    for row in rows:
        d = datetime.strptime(row["date"], "%Y-%m-%d").date()
        if (current - d).days <= 1:
            streak += 1; current = d
        else: break
    return streak


# ══════════════════════════════════════════════════════════════════
#  СИСТЕМНЫЙ ПРОМПТ ALEX
# ══════════════════════════════════════════════════════════════════

def build_system(uid: int, mode: str = "general") -> str:
    lang  = get_lang(uid)
    level = get_level(uid)
    lang_instruction = "Explain everything in Russian. Use Russian for all explanations, but keep English examples and exercises in English." if lang == "ru" else "Use English for everything."

    base = f"""You are ALEX, a professional English language tutor with 15 years of experience.
You specialize in teaching students from {level} level upward.
Student's current level: {level}

{lang_instruction}

YOUR TEACHING STYLE:
- Patient, encouraging, and precise
- Always explain WHY something is correct or wrong, not just WHAT
- Give concrete examples after every rule
- Adapt complexity to the student's level ({level})
- Celebrate progress, normalize mistakes as part of learning
- When correcting errors: first acknowledge what's good, then correct gently

FORMATTING (Telegram HTML only):
<b>bold</b> for key terms, <i>italic</i> for examples, <code>code</code> for grammar patterns
NO markdown: *, _, #
Use emoji for structure: 📌 rules, ✅ correct, ❌ wrong, 💡 tips
Always respond according to the language instruction above.
"""

    if mode == "correction":
        base += """
CORRECTION MODE:
When the student writes in English:
1. Acknowledge any good points first
2. List ALL errors clearly with the category (grammar/vocabulary/spelling/punctuation)
3. Show corrected version
4. Explain each error in a clear, educational way
5. Give one extra tip related to the most important error
Format: use numbered list for errors, show original → corrected
"""
    elif mode == "toefl":
        base += """
TOEFL MODE:
You are a TOEFL iBT specialist. You know:
- Reading: academic passages, question types (factual, inference, vocabulary, rhetorical purpose, insert text)
- Listening: lectures, conversations, question types (main idea, detail, inference, attitude, purpose)
- Speaking: integrated and independent tasks, scoring rubrics (delivery, language use, topic development)
- Writing: integrated essay (reading+lecture), independent essay, scoring criteria (development, organization, language use)
Target score guidance, time management strategies, common traps and how to avoid them.
Always provide practice materials at appropriate difficulty (B2-C1 level for TOEFL).
"""
    elif mode == "test":
        base += """
TEST MODE:
Create challenging, exam-quality questions. 
For grammar tests: use multiple choice (4 options), sentence transformation, error identification.
For vocabulary: definitions, context fill-in, synonyms/antonyms, collocations.
For reading: use authentic-style academic or semi-academic passages (150-300 words), then ask 5 comprehension questions.
Always explain the correct answers after the student responds.
Track score and give percentage at the end.
"""
    elif mode == "vocab":
        base += """
VOCABULARY MODE:
Teach vocabulary systematically:
- Word family (noun/verb/adjective/adverb forms)
- Collocations (what words go together)
- Register (formal/informal/neutral)
- Common mistakes with this word
- 2-3 example sentences showing different uses
- Memory tip or mnemonic when helpful
Focus on vocabulary that is useful for the student's level and TOEFL if relevant.
"""
    elif mode == "speaking":
        base += """
SPEAKING PRACTICE MODE:
Simulate a natural conversation. 
- Ask follow-up questions to keep the conversation going
- When the student makes errors, note them but continue the conversation naturally
- At the end of each exchange, give a brief correction summary
- For TOEFL Speaking practice: give a topic, time limit (15s prep, 45s response for independent; 30s prep, 60s response for integrated)
- Score speaking responses on: delivery, language use, topic development (like real TOEFL rubric)
"""

    return base


# ══════════════════════════════════════════════════════════════════
#  ИСТОРИЯ И СОСТОЯНИЯ
# ══════════════════════════════════════════════════════════════════

histories: dict[int, list[dict]] = {}
waiting:   dict[int, str]        = {}
session_data: dict[int, dict]    = {}  # временные данные текущей сессии

def get_history(uid): return histories.setdefault(uid, [])

def add_message(uid: int, role: str, content):
    h = get_history(uid)
    h.append({"role": role, "content": content})
    if len(h) > 30: histories[uid] = h[-30:]

def clear_history(uid): histories[uid] = []

def set_session(uid: int, **kwargs):
    session_data.setdefault(uid, {}).update(kwargs)

def get_session(uid: int) -> dict:
    return session_data.get(uid, {})

def clear_session(uid: int):
    session_data.pop(uid, None)


# ══════════════════════════════════════════════════════════════════
#  ANTHROPIC API
# ══════════════════════════════════════════════════════════════════

CMD_FOOTER_SEP = "\n\n<i>─────────────────────────────────</i>\n<i>"

async def ask_alex(uid: int, user_text: str, mode: str = "general", extra: str = "") -> str:
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
                return f"⚠️ <code>{data['error'].get('message','')[:120]}</code>"
            reply = data["content"][0]["text"].strip()
    except Exception as e:
        return f"⚠️ Error. Try again.\n<i>{str(e)[:80]}</i>"

    add_message(uid, "assistant", reply)
    lang   = get_lang(uid)
    footer = CMD_FOOTER_SEP + t("cmd_footer", lang) + "</i>"
    return reply + footer


async def analyze_photo_text(uid: int, photo_bytes: bytes) -> str:
    b64  = base64.standard_b64encode(photo_bytes).decode()
    lang = get_lang(uid)
    prompt = (
        "The photo contains text in English. Please:\n"
        "1. Extract all the text you can see\n"
        "2. Identify any grammar or spelling errors\n"
        "3. Explain any difficult vocabulary\n"
        "4. If it's an exercise or test, help solve it with explanations\n"
        "5. Give overall feedback on the text quality"
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
            footer = CMD_FOOTER_SEP + t("cmd_footer", lang) + "</i>"
            return data["content"][0]["text"].strip() + footer
    except Exception as e:
        return f"⚠️ <i>{str(e)[:100]}</i>"


# ══════════════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════════

def main_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_lesson",lang)),    KeyboardButton(text=t("btn_vocab",lang))],
            [KeyboardButton(text=t("btn_talk",lang)),      KeyboardButton(text=t("btn_test",lang))],
            [KeyboardButton(text=t("btn_toefl",lang)),     KeyboardButton(text=t("btn_writing",lang))],
            [KeyboardButton(text=t("btn_idioms",lang)),    KeyboardButton(text=t("btn_dictation",lang))],
            [KeyboardButton(text=t("btn_mistakes",lang)),  KeyboardButton(text=t("btn_stats",lang))],
        ],
        resize_keyboard=True,
        input_field_placeholder=t("input_placeholder", lang),
    )


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
         InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")],
    ])


def level_kb(lang: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=lv, callback_data=f"level_{lv}")] for lv in LEVELS]
    rows.append([InlineKeyboardButton(text=t("level_test_btn", lang), callback_data="level_test")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def toefl_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["toefl_reading","toefl_listening","toefl_speaking","toefl_writing","toefl_full","toefl_strategy","toefl_score"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def test_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["test_grammar","test_vocab","test_reading","test_mixed","test_placement"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def talk_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["talk_daily","talk_travel","talk_work","talk_culture","talk_debate","talk_business","talk_free"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lesson_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["lesson_tenses","lesson_conditionals","lesson_modal","lesson_passive",
            "lesson_articles","lesson_prepositions","lesson_phrasal","lesson_reported"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vocab_kb(lang: str) -> InlineKeyboardMarkup:
    keys = ["vocab_new","vocab_review","vocab_topic","vocab_flashcards"]
    rows = [[InlineKeyboardButton(text=t(k,lang), callback_data=k)] for k in keys]
    rows.append([InlineKeyboardButton(text=t("btn_back",lang), callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def remind_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌅 08:00", callback_data="remind_08:00"),
         InlineKeyboardButton(text="☀️ 10:00", callback_data="remind_10:00")],
        [InlineKeyboardButton(text="🌞 12:00", callback_data="remind_12:00"),
         InlineKeyboardButton(text="🌇 18:00", callback_data="remind_18:00")],
        [InlineKeyboardButton(text="🌆 19:00", callback_data="remind_19:00"),
         InlineKeyboardButton(text="🌙 21:00", callback_data="remind_21:00")],
        [InlineKeyboardButton(text="❌ Отключить / Disable", callback_data="remind_off")],
    ])


# ══════════════════════════════════════════════════════════════════
#  ПРОМПТЫ ДЛЯ РАЗДЕЛОВ
# ══════════════════════════════════════════════════════════════════

LESSON_PROMPTS = {
    "lesson_tenses":      "Teach me English verb tenses systematically. Start with an overview, then cover each tense with: form, usage, examples, common mistakes. Include a mini-quiz at the end.",
    "lesson_conditionals":"Teach me all types of English conditionals (0,1,2,3 and mixed). Explain when to use each, give clear examples, then test me with 5 sentences to complete.",
    "lesson_modal":       "Teach me English modal verbs (can, could, may, might, must, shall, should, will, would, ought to, need to). Cover meaning, usage differences, and give practice exercises.",
    "lesson_passive":     "Teach me the passive voice in English. Cover all tenses in passive, when and why we use passive, with examples and transformation exercises.",
    "lesson_articles":    "Teach me English articles (a/an/the/zero article). This is one of the hardest topics. Cover all rules with clear examples and exceptions, then give practice exercises.",
    "lesson_prepositions":"Teach me English prepositions of time, place, and movement. Cover the most common ones with examples and give exercises.",
    "lesson_phrasal":     "Teach me the most important English phrasal verbs. Organize by verb (get, take, give, put, come, go, look, turn). Give meanings and example sentences, then practice exercises.",
    "lesson_reported":    "Teach me reported speech in English. Cover statements, questions, and commands, plus the backshift of tenses. Give transformation exercises.",
}

TALK_PROMPTS = {
    "talk_daily":    "Let's have a conversation about daily life. Start by asking me about my typical day, then keep the conversation going naturally. Correct my English gently.",
    "talk_travel":   "Let's talk about travel. Ask me about places I've been or want to visit, and share some interesting discussion points. Correct my errors naturally.",
    "talk_work":     "Let's practice professional English. Simulate a work-related conversation — could be a job interview, meeting, or workplace discussion. Correct my language.",
    "talk_culture":  "Let's discuss culture, arts, movies, or music. Start a conversation on an interesting cultural topic suited to my level.",
    "talk_debate":   "Let's do a debate exercise. Give me a controversial topic, state one position, and I'll argue the other. This will improve my argumentative English.",
    "talk_business": "Let's practice Business English. We'll do a business scenario — negotiation, presentation, email discussion, or meeting. Use formal business language.",
    "talk_free":     "Let's have a free conversation in English. Ask me what I'd like to talk about and keep it natural. Correct my mistakes gently after each response.",
}

TEST_PROMPTS = {
    "test_grammar":    "Create a grammar test with 10 challenging multiple-choice questions appropriate for my level. Cover different grammar areas. After I answer, give full explanations.",
    "test_vocab":      "Create a vocabulary test with 10 questions: definitions, fill-in-the-blank, and synonym/antonym questions. After I answer, explain all answers.",
    "test_reading":    "Create a reading comprehension test: give me an academic-style passage (250-300 words), then ask 5 comprehension questions (factual, inference, vocabulary in context). Score me at the end.",
    "test_mixed":      "Create a comprehensive mixed test with 15 questions covering grammar, vocabulary, and usage. Make it challenging but appropriate for my level.",
    "test_placement":  "Run a placement test to determine my exact English level. Ask me 15 progressively harder questions covering grammar, vocabulary, and comprehension. At the end, tell me my level with detailed feedback.",
}

TOEFL_PROMPTS = {
    "toefl_reading":   "Give me a TOEFL Reading practice passage (academic topic, approximately 300 words at B2-C1 level), followed by 5 TOEFL-style questions (factual information, inference, vocabulary in context, rhetorical purpose, sentence insertion). After I answer, give detailed explanations and a score.",
    "toefl_listening": "Simulate a TOEFL Listening exercise. Describe a university lecture or conversation in detail (as if I'm reading the transcript), then ask 5 TOEFL-style listening comprehension questions. Give explanations and score.",
    "toefl_speaking":  "Give me a TOEFL Speaking practice task. Start with an Independent Speaking task: give me a topic, tell me I have 15 seconds to prepare and 45 seconds to respond. After I write my response, score it on the TOEFL 0-4 scale for: Delivery, Language Use, and Topic Development. Give detailed feedback.",
    "toefl_writing":   "Give me a TOEFL Writing practice task. Start with the Independent Writing task: give me a question/prompt, tell me to write a 5-paragraph essay of at least 300 words. After I submit, score it 1-5 on: Development & Support, Organization, and Language Use. Give detailed feedback with specific improvements.",
    "toefl_full":      "Let's do a mini TOEFL iBT simulation. We'll do one task from each section in order: 1) Reading (short passage + 3 questions), 2) Listening (transcript + 2 questions), 3) Speaking (1 independent task), 4) Writing (1 short essay). Give me a final score estimate at the end.",
    "toefl_strategy":  "Give me comprehensive TOEFL iBT strategies and tips for all 4 sections. Include: time management, common question types and how to approach them, common mistakes to avoid, and what to focus on to maximize my score. Base advice on my current level.",
}

VOCAB_TOPIC_PROMPTS = {
    "vocab_new":        "Teach me 10 new English words appropriate for my level. For each word give: pronunciation guide, part of speech, definition, 2 example sentences, common collocations, and a memory tip.",
    "vocab_review":     "Quiz me on vocabulary I might have learned. Give me 10 words and ask me to define them or use them in a sentence. Give feedback and explanations after each answer.",
    "vocab_topic":      "Ask me what topic I want vocabulary for, then teach me 10-15 essential words on that topic with definitions, examples, and collocations.",
    "vocab_flashcards": "Give me 10 vocabulary flashcard-style prompts: show the definition or a gapped sentence, let me guess the word, then confirm or correct. Keep score.",
}


# ══════════════════════════════════════════════════════════════════
#  ПЛАНИРОВЩИК
# ══════════════════════════════════════════════════════════════════

async def send_reminder(uid: int):
    user = get_user(uid)
    if not user: return
    lang   = user.get("lang", "ru")
    streak = get_streak(uid)
    msgs_ru = [
        "📚 <b>Время заниматься английским!</b>\n\nДаже 15 минут в день дают результат. ALEX ждёт! 🎓",
        "🔥 <b>Не пропусти урок!</b>\n\nТвой английский улучшается с каждым занятием.",
        "⚡️ <b>Ежедневная практика = быстрый прогресс.</b>\n\nНачни прямо сейчас!",
    ]
    msgs_en = [
        "📚 <b>Time to practice English!</b>\n\nEven 15 minutes a day makes a difference. ALEX is ready! 🎓",
        "🔥 <b>Don't miss your lesson!</b>\n\nYour English improves with every session.",
        "⚡️ <b>Daily practice = fast progress.</b>\n\nStart right now!",
    ]
    msgs = msgs_ru if lang == "ru" else msgs_en
    text = random.choice(msgs)
    if streak > 1:
        text += f"\n\n{t('streak_msg', lang, n=streak)}"
    try:
        await bot.send_message(uid, text)
    except Exception as e:
        logger.warning(f"Reminder failed {uid}: {e}")


async def send_weekly_report(uid: int):
    stats = get_stats(uid)
    lang  = get_lang(uid)
    level = get_level(uid)
    titles = {"ru": "📊 <b>Еженедельный отчёт</b>", "en": "📊 <b>Weekly Report</b>"}
    try:
        await bot.send_message(uid,
            f"{titles.get(lang,'📊 Report')}\n\n"
            f"🎯 {level} · 🔥 {stats['streak']} · 📅 {stats['sessions']}\n"
            f"📝 {stats['words']} words · ✅ {stats['tests']} tests"
        )
    except Exception: pass


def schedule_all():
    rows = db("SELECT uid, remind_time FROM users WHERE remind_time IS NOT NULL AND remind_time != 'off'", fetch=True)
    if rows:
        for r in rows:
            try:
                h, m = map(int, r["remind_time"].split(":"))
                scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[r["uid"]], id=f"remind_{r['uid']}", replace_existing=True)
            except Exception: pass
    all_users = db("SELECT uid FROM users", fetch=True)
    if all_users:
        for r in all_users:
            scheduler.add_job(send_weekly_report, "cron", day_of_week="sun", hour=19, args=[r["uid"]], id=f"weekly_{r['uid']}", replace_existing=True)


# ══════════════════════════════════════════════════════════════════
#  КОМАНДЫ
# ══════════════════════════════════════════════════════════════════

@dp.message(Command("start"))
async def cmd_start(message: Message):
    uid  = message.from_user.id
    name = message.from_user.first_name or "Student"
    upsert_user(uid, name)
    scheduler.add_job(send_weekly_report, "cron", day_of_week="sun", hour=19, args=[uid], id=f"weekly_{uid}", replace_existing=True)
    user = get_user(uid)
    if not user or not user.get("lang"):
        await message.answer(t("choose_lang","ru"), reply_markup=lang_kb())
        return
    lang = get_lang(uid)
    await message.answer(t("welcome", lang, name=name), reply_markup=main_kb(lang))
    await message.answer(t("choose_level", lang), reply_markup=level_kb(lang))


@dp.message(Command("lang"))
async def cmd_lang(message: Message):
    await message.answer(t("choose_lang", get_lang(message.from_user.id)), reply_markup=lang_kb())


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(t("help", get_lang(message.from_user.id)))


@dp.message(Command("level"))
async def cmd_level(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    await message.answer(t("choose_level", lang), reply_markup=level_kb(lang))


@dp.message(Command("lesson"))
async def cmd_lesson(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t("lesson_menu", lang), reply_markup=lesson_kb(lang))


@dp.message(Command("vocab"))
async def cmd_vocab(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t("vocab_menu", lang), reply_markup=vocab_kb(lang))


@dp.message(Command("talk"))
async def cmd_talk(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t("talk_menu", lang), reply_markup=talk_kb(lang))


@dp.message(Command("test"))
async def cmd_test(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t("test_menu", lang), reply_markup=test_kb(lang))


@dp.message(Command("toefl"))
async def cmd_toefl(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(t("toefl_menu", lang), reply_markup=toefl_kb(lang))


@dp.message(Command("writing"))
async def cmd_writing(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    await message.answer(t("writing_prompt", lang))
    waiting[uid] = "writing"


@dp.message(Command("dictation"))
async def cmd_dictation(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    level = get_level(uid)
    await message.answer(t("dictation_ready", lang))
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_alex(uid,
        f"Give me one dictation sentence appropriate for {level} level. "
        "Format: 'DICTATION: [sentence]' then below write 'Write exactly what you read above.'",
        mode="general"
    )
    await message.answer(reply)
    waiting[uid] = "dictation"
    log_session(uid, "dictation")


@dp.message(Command("idioms"))
async def cmd_idioms(message: Message):
    uid  = message.from_user.id
    level = get_level(uid)
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_alex(uid,
        f"Teach me 5 useful English idioms and phrasal verbs appropriate for {level} level. "
        "For each: the idiom, meaning, origin (briefly), 2 example sentences, and when/how to use it naturally.",
        mode="vocab"
    )
    await message.answer(reply)
    log_session(uid, "idioms")


@dp.message(Command("reading"))
async def cmd_reading(message: Message):
    uid = message.from_user.id
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_alex(uid, TEST_PROMPTS["test_reading"], mode="test")
    await message.answer(reply)
    log_session(uid, "test_reading")
    waiting[uid] = "test_active"


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    stats = get_stats(uid)
    level = get_level(uid)
    await message.answer(t("stats_title", lang,
        level=level, sessions=stats["sessions"], streak=stats["streak"],
        tests=stats["tests"], words=stats["words"], errors=stats["errors"], toefl=stats["toefl"]
    ))


@dp.message(Command("mistakes"))
async def cmd_mistakes(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    rows = db("SELECT category, original, corrected, explanation FROM mistakes WHERE uid=? ORDER BY created_at DESC LIMIT 10", (uid,), fetch=True)
    if not rows:
        await message.answer(t("mistakes_empty", lang))
        return
    text = t("mistakes_title", lang)
    for i, r in enumerate(rows, 1):
        text += f"{i}. ❌ <code>{r['original'][:50]}</code>\n   ✅ <code>{r['corrected'][:50]}</code>\n   <i>{r['explanation'][:100]}</i>\n\n"
    await message.answer(text)


@dp.message(Command("streak"))
async def cmd_streak(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    streak = get_streak(uid)
    if streak > 1:
        await message.answer(t("streak_msg", lang, n=streak))
    else:
        msgs = {"ru": "🎯 Начни стрик сегодня — занимайся каждый день!", "en": "🎯 Start your streak today — study every day!"}
        await message.answer(msgs.get(lang, msgs["en"]))


@dp.message(Command("remind"))
async def cmd_remind(message: Message):
    await message.answer("⏰ Choose reminder time:", reply_markup=remind_kb())


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    clear_history(uid)
    waiting.pop(uid, None)
    clear_session(uid)
    await message.answer(t("reset_done", lang))


# ══════════════════════════════════════════════════════════════════
#  CALLBACK
# ══════════════════════════════════════════════════════════════════

@dp.callback_query(F.data.startswith("lang_"))
async def cb_lang(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = cb.data.replace("lang_","")
    update_user(uid, lang=lang)
    name = cb.from_user.first_name or "Student"
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("✅")
    await cb.message.answer(t("welcome", lang, name=name), reply_markup=main_kb(lang))
    await cb.message.answer(t("choose_level", lang), reply_markup=level_kb(lang))


@dp.callback_query(F.data == "back_main")
async def cb_back(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer("Menu:", reply_markup=main_kb(lang))
    await cb.answer()


@dp.callback_query(F.data.startswith("level_"))
async def cb_level(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)
    data = cb.data.replace("level_","")

    if data == "test":
        # Запускаем тест определения уровня
        await cb.answer()
        await cb.message.edit_reply_markup(reply_markup=None)
        await bot.send_chat_action(cb.message.chat.id, "typing")
        reply = await ask_alex(uid, TEST_PROMPTS["test_placement"], mode="test")
        await cb.message.answer(reply)
        waiting[uid] = "placement_test"
        log_session(uid, "placement_test")
        return

    update_user(uid, level=data)
    await cb.answer(f"✅ Level: {data}")
    await cb.message.edit_reply_markup(reply_markup=None)
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid,
        f"My English level is {data}. Give me a brief welcome message, tell me what we'll focus on at this level, and suggest what to start with today.",
        mode="general"
    )
    await cb.message.answer(reply)
    log_session(uid, "level_set")


@dp.callback_query(F.data.startswith("lesson_"))
async def cb_lesson(cb: CallbackQuery):
    uid    = cb.from_user.id
    prompt = LESSON_PROMPTS.get(cb.data, "")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="general")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "lesson_active"


@dp.callback_query(F.data.startswith("talk_"))
async def cb_talk(cb: CallbackQuery):
    uid    = cb.from_user.id
    prompt = TALK_PROMPTS.get(cb.data, "")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="speaking")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "speaking_active"


@dp.callback_query(F.data.startswith("test_"))
async def cb_test(cb: CallbackQuery):
    uid    = cb.from_user.id
    prompt = TEST_PROMPTS.get(cb.data, "")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="test")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "test_active"


@dp.callback_query(F.data.startswith("toefl_"))
async def cb_toefl(cb: CallbackQuery):
    uid    = cb.from_user.id
    lang   = get_lang(uid)

    if cb.data == "toefl_score":
        # Показываем баллы
        await cb.answer()
        rows = db("SELECT section, AVG(score) as avg, MAX(score) as best, COUNT(*) as cnt FROM toefl_scores WHERE uid=? GROUP BY section", (uid,), fetch=True)
        if not rows:
            msg = "🎓 No TOEFL scores yet. Start practicing!" if lang=="en" else "🎓 Баллов TOEFL пока нет. Начни практиковаться!"
            await cb.message.answer(msg)
            return
        text = "🎓 <b>TOEFL Progress:</b>\n\n"
        for r in rows:
            text += f"📌 <b>{r['section']}</b>: best {r['best']}, avg {r['avg']:.0f} ({r['cnt']} sessions)\n"
        await cb.message.answer(text)
        return

    prompt = TOEFL_PROMPTS.get(cb.data, "")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="toefl")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "toefl_active"


@dp.callback_query(F.data.startswith("vocab_"))
async def cb_vocab(cb: CallbackQuery):
    uid    = cb.from_user.id
    prompt = VOCAB_TOPIC_PROMPTS.get(cb.data, "")
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="vocab")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "vocab_active"


@dp.callback_query(F.data.startswith("remind_"))
async def cb_remind(cb: CallbackQuery):
    uid  = cb.from_user.id
    data = cb.data.replace("remind_","")
    if data == "off":
        update_user(uid, remind_time="off")
        if scheduler.get_job(f"remind_{uid}"): scheduler.remove_job(f"remind_{uid}")
        await cb.answer("Disabled")
        await cb.message.edit_text("❌ Reminders disabled.")
    else:
        update_user(uid, remind_time=data)
        try:
            h, m = map(int, data.split(":"))
            scheduler.add_job(send_reminder,"cron",hour=h,minute=m,args=[uid],id=f"remind_{uid}",replace_existing=True)
        except Exception as e: logger.warning(e)
        await cb.answer(f"✅ {data}")
        await cb.message.edit_text(f"✅ <b>Reminder set: {data}</b>\n\nI'll message you every day! 📚")


# ══════════════════════════════════════════════════════════════════
#  ФОТО
# ══════════════════════════════════════════════════════════════════

@dp.message(F.photo)
async def handle_photo(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    photo: PhotoSize = message.photo[-1]
    file_info   = await bot.get_file(photo.file_id)
    file_bytes  = await bot.download_file(file_info.file_path)
    photo_bytes = file_bytes.read() if hasattr(file_bytes,"read") else bytes(file_bytes)
    await message.answer(t("photo_received", lang))
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await analyze_photo_text(uid, photo_bytes)
    await message.answer(reply)
    log_session(uid, "photo_analysis")


# ══════════════════════════════════════════════════════════════════
#  СВОБОДНЫЙ ТЕКСТ
# ══════════════════════════════════════════════════════════════════

@dp.message(F.text)
async def handle_text(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    text  = message.text.strip()
    state = waiting.get(uid, "")

    # ── Кнопки меню ───────────────────────────────────────────────
    menu_map = {
        t("btn_lesson",lang):    "lesson",
        t("btn_vocab",lang):     "vocab",
        t("btn_talk",lang):      "talk",
        t("btn_test",lang):      "test",
        t("btn_toefl",lang):     "toefl",
        t("btn_writing",lang):   "writing",
        t("btn_idioms",lang):    "idioms",
        t("btn_dictation",lang): "dictation",
        t("btn_mistakes",lang):  "mistakes",
        t("btn_stats",lang):     "stats",
    }
    action = menu_map.get(text)
    if action:
        if action == "lesson":    await cmd_lesson(message)
        elif action == "vocab":   await cmd_vocab(message)
        elif action == "talk":    await cmd_talk(message)
        elif action == "test":    await cmd_test(message)
        elif action == "toefl":   await cmd_toefl(message)
        elif action == "writing": await cmd_writing(message)
        elif action == "idioms":  await cmd_idioms(message)
        elif action == "dictation": await cmd_dictation(message)
        elif action == "mistakes": await cmd_mistakes(message)
        elif action == "stats":   await cmd_stats(message)
        return

    # ── Режим написания/проверки ───────────────────────────────────
    if state == "writing":
        waiting.pop(uid, None)
        await bot.send_chat_action(message.chat.id, "typing")
        # Сначала исправляем, потом логируем
        reply = await ask_alex(uid,
            f"Please correct and analyze this text:\n\n{text}",
            mode="correction",
            extra="Be thorough: find ALL errors, explain each one clearly, show the corrected version."
        )
        await message.answer(reply)
        log_session(uid, "writing_check")
        # Логируем как ошибки если текст содержит английский
        if len(text) > 20:
            log_mistake(uid, text[:100], "See correction above", "writing exercise", "mixed")
        return

    # ── Активные сессии (тест, разговор, TOEFL и т.д.) ────────────
    if state in ("test_active","lesson_active","speaking_active","toefl_active","vocab_active","placement_test","dictation"):
        # Продолжаем текущую сессию
        mode_map = {
            "test_active": "test", "lesson_active": "general",
            "speaking_active": "speaking", "toefl_active": "toefl",
            "vocab_active": "vocab", "placement_test": "test", "dictation": "general",
        }
        mode = mode_map.get(state, "general")
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode=mode)
        await message.answer(reply)

        # Если текст на английском — автопроверка на ошибки в фоне
        english_ratio = sum(1 for c in text if c.isalpha() and ord(c) < 128) / max(len(text),1)
        if english_ratio > 0.5 and len(text) > 15 and state == "speaking_active":
            log_session(uid, "speaking_practice")
        return

    # ── Автоопределение: пользователь пишет по-английски ──────────
    english_ratio = sum(1 for c in text if c.isalpha() and ord(c) < 128) / max(len(text), 1)

    if english_ratio > 0.6 and len(text) > 10:
        # Текст явно на английском — исправляем ошибки + отвечаем
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid,
            text,
            mode="correction",
            extra="The student wrote something in English. First, correct any errors if there are any (if perfect, compliment them). Then respond naturally to what they said or asked."
        )
        await message.answer(reply)
        log_session(uid, "free_writing")
    else:
        # Вопрос на родном языке — просто отвечаем как репетитор
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
    logger.info("🎓 LinguaMax ALEX — запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
