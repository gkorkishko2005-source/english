"""
╔══════════════════════════════════════════════════════════════════╗
   LinguaMax · Репетитор ALEX — Ultimate Edition
   aiogram 3.x · Anthropic Claude Haiku · SQLite · APScheduler

   ФУНКЦИИ:
   ✅ Ситуативный диалог / Roleplay (7 сценариев + свой)
   ✅ 3-слойная коррекция текста (исправленный / native-like / разбор)
   ✅ "Объясни иначе" — новая аналогия по запросу
   ✅ Конструктор предложений
   ✅ 10 тем грамматики с интерактивными упражнениями
   ✅ Словарь с SM-2 интервальным повторением (как Anki)
   ✅ Ежедневный квиз из личного словаря
   ✅ Тесты: грамматика, словарь, чтение, письмо, смешанный
   ✅ TOEFL iBT: все 4 секции + стратегии + мини-симуляция
   ✅ Разговорная практика (7 тем)
   ✅ XP + ранги + стрик занятий
   ✅ База ошибок + статистика
   ✅ Анализ фото с текстом
   ✅ Персонализация по интересам
   ✅ Ежедневные напоминания
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import base64
import logging
import os
import random

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

from database import (
    db_init, get_user, get_lang, get_level, get_interests,
    upsert_user, update_user, add_xp, update_streak,
    get_streak_count, get_xp, get_rank,
    add_word, get_due_words, update_word_review, get_word_count,
    log_mistake, get_mistakes,
    log_session, get_full_stats,
    log_toefl, get_toefl_scores,
)
from prompts import (
    build_system,
    ROLEPLAY_SCENARIOS, LESSON_PROMPTS, VOCAB_PROMPTS,
    TEST_PROMPTS, TOEFL_PROMPTS, TALK_PROMPTS,
)

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
#  ИСТОРИЯ И СОСТОЯНИЯ
# ══════════════════════════════════════════════════════════════════

histories:  dict[int, list[dict]] = {}
waiting:    dict[int, str]        = {}
session_ctx: dict[int, dict]      = {}  # контекст текущей сессии

def get_history(uid): return histories.setdefault(uid, [])

def add_message(uid: int, role: str, content):
    h = get_history(uid)
    h.append({"role": role, "content": content})
    if len(h) > 30: histories[uid] = h[-30:]

def clear_history(uid): histories[uid] = []

def set_ctx(uid: int, **kw): session_ctx.setdefault(uid, {}).update(kw)
def get_ctx(uid: int) -> dict: return session_ctx.get(uid, {})
def clear_ctx(uid: int): session_ctx.pop(uid, None)

# ══════════════════════════════════════════════════════════════════
#  ANTHROPIC API
# ══════════════════════════════════════════════════════════════════

FOOTER = "\n\n<i>──────────────────────────────────</i>\n<i>/lesson · /vocab · /test · /toefl · /roleplay · /talk · /help</i>"

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
                return f"⚠️ <code>{data['error'].get('message','')[:150]}</code>"
            reply = data["content"][0]["text"].strip()
    except Exception as e:
        return f"⚠️ Connection error. Try again.\n<i>{str(e)[:80]}</i>"
    add_message(uid, "assistant", reply)
    return reply + FOOTER


async def analyze_photo(uid: int, photo_bytes: bytes) -> str:
    b64 = base64.standard_b64encode(photo_bytes).decode()
    lang = get_lang(uid)
    prompt = (
        "Analyze the English text in this photo:\n"
        "1. Extract all visible text\n"
        "2. Correct any grammar/spelling errors\n"
        "3. Explain difficult vocabulary in context\n"
        "4. If it's an exercise — solve it with explanations\n"
        "5. Rate the overall English quality"
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
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════════════════════════════

def main_kb(lang: str) -> ReplyKeyboardMarkup:
    if lang == "ru":
        rows = [
            ["📚 Урок грамматики",    "📝 Словарь"],
            ["🎭 Ролевой диалог",     "✅ Тест"],
            ["🎓 TOEFL",              "✍️ Проверить текст"],
            ["💬 Разговор",           "🗣 Идиомы"],
            ["❌ Мои ошибки",         "📊 Прогресс"],
        ]
        placeholder = "Напиши по-английски — ALEX проверит..."
    else:
        rows = [
            ["📚 Grammar Lesson",    "📝 Vocabulary"],
            ["🎭 Roleplay",          "✅ Test"],
            ["🎓 TOEFL",             "✍️ Check Writing"],
            ["💬 Speaking",          "🗣 Idioms"],
            ["❌ My Mistakes",       "📊 Progress"],
        ]
        placeholder = "Write in English — ALEX will check..."
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
        input_field_placeholder=placeholder,
    )


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en"),
    ]])


def level_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=lv, callback_data=f"setlevel_{lv}")] for lv in LEVELS]
    rows.append([InlineKeyboardButton(text="🔍 Placement test", callback_data="test_placement")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def roleplay_kb(lang: str) -> InlineKeyboardMarkup:
    rows = []
    for key, data in ROLEPLAY_SCENARIOS.items():
        label = data["ru"] if lang == "ru" else data["en"]
        rows.append([InlineKeyboardButton(text=label, callback_data=key)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def lesson_kb(lang: str) -> InlineKeyboardMarkup:
    labels = {
        "lesson_tenses":       ("⏰ Времена глагола",         "⏰ Verb Tenses"),
        "lesson_conditionals": ("🔀 Условные предложения",    "🔀 Conditionals"),
        "lesson_modal":        ("💭 Модальные глаголы",       "💭 Modal Verbs"),
        "lesson_passive":      ("🔄 Пассивный залог",         "🔄 Passive Voice"),
        "lesson_articles":     ("📌 Артикли",                 "📌 Articles"),
        "lesson_prepositions": ("📍 Предлоги",                "📍 Prepositions"),
        "lesson_phrasal":      ("🔗 Фразовые глаголы",        "🔗 Phrasal Verbs"),
        "lesson_reported":     ("💬 Косвенная речь",          "💬 Reported Speech"),
        "lesson_subjunctive":  ("🌙 Сослагательное наклонение","🌙 Subjunctive"),
        "lesson_inversion":    ("🔁 Инверсия (C1-C2)",        "🔁 Inversion (C1-C2)"),
    }
    rows = []
    for key, (ru, en) in labels.items():
        label = ru if lang == "ru" else en
        rows.append([InlineKeyboardButton(text=label, callback_data=key)])
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vocab_kb(lang: str) -> InlineKeyboardMarkup:
    labels = {
        "vocab_new":         ("🆕 Новые слова",           "🆕 New Words"),
        "vocab_review":      ("🔄 Повторение (SM-2)",     "🔄 Review (SM-2)"),
        "vocab_flashcards":  ("🃏 Флэш-карточки",         "🃏 Flashcards"),
        "vocab_collocations":("🤝 Коллокации",            "🤝 Collocations"),
        "vocab_idioms_adv":  ("🗣 Продвинутые идиомы",    "🗣 Advanced Idioms"),
        "vocab_topic":       ("📂 По теме",               "📂 By Topic"),
        "daily_quiz":        ("📅 Квиз из словаря",       "📅 Daily Word Quiz"),
    }
    rows = []
    for key, (ru, en) in labels.items():
        label = ru if lang == "ru" else en
        rows.append([InlineKeyboardButton(text=label, callback_data=key)])
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def test_kb(lang: str) -> InlineKeyboardMarkup:
    labels = {
        "test_grammar":   ("📐 Грамматика",            "📐 Grammar"),
        "test_vocab":     ("📝 Лексика",               "📝 Vocabulary"),
        "test_reading":   ("📖 Чтение (TOEFL-style)",  "📖 Reading (TOEFL-style)"),
        "test_writing":   ("✍️ Письмо",                "✍️ Writing"),
        "test_mixed":     ("🎲 Смешанный",             "🎲 Mixed"),
        "test_placement": ("🔍 Определение уровня",    "🔍 Placement Test"),
    }
    rows = []
    for key, (ru, en) in labels.items():
        label = ru if lang == "ru" else en
        rows.append([InlineKeyboardButton(text=label, callback_data=key)])
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def toefl_kb(lang: str) -> InlineKeyboardMarkup:
    labels = {
        "toefl_reading":   ("📖 Reading",            "📖 Reading"),
        "toefl_listening": ("🎧 Listening",           "🎧 Listening"),
        "toefl_speaking1": ("🗣 Speaking — Independent", "🗣 Speaking — Independent"),
        "toefl_speaking2": ("🗣 Speaking — Integrated",  "🗣 Speaking — Integrated"),
        "toefl_writing1":  ("✍️ Writing — Independent",  "✍️ Writing — Independent"),
        "toefl_writing2":  ("✍️ Writing — Integrated",   "✍️ Writing — Integrated"),
        "toefl_full":      ("🏆 Полный мини-тест",    "🏆 Full Mini-Test"),
        "toefl_strategy":  ("💡 Стратегии и советы",  "💡 Tips & Strategies"),
        "toefl_score":     ("📊 Мои баллы",           "📊 My Scores"),
    }
    rows = []
    for key, (ru, en) in labels.items():
        label = ru if lang == "ru" else en
        rows.append([InlineKeyboardButton(text=label, callback_data=key)])
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def talk_kb(lang: str) -> InlineKeyboardMarkup:
    labels = {
        "talk_daily":     ("☀️ Повседневная жизнь",  "☀️ Daily Life"),
        "talk_travel":    ("✈️ Путешествия",          "✈️ Travel"),
        "talk_work":      ("💼 Работа",              "💼 Work"),
        "talk_debate":    ("🗣 Дебаты",              "🗣 Debate"),
        "talk_business":  ("🤝 Бизнес English",      "🤝 Business English"),
        "talk_free":      ("💭 Свободная беседа",    "💭 Free Chat"),
        "talk_interview": ("👔 Mock Interview",      "👔 Mock Interview"),
    }
    rows = []
    for key, (ru, en) in labels.items():
        label = ru if lang == "ru" else en
        rows.append([InlineKeyboardButton(text=label, callback_data=key)])
    rows.append([InlineKeyboardButton(text="← Назад" if lang=="ru" else "← Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def remind_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌅 08:00", callback_data="remind_08:00"),
         InlineKeyboardButton(text="☀️ 10:00", callback_data="remind_10:00")],
        [InlineKeyboardButton(text="🌞 12:00", callback_data="remind_12:00"),
         InlineKeyboardButton(text="🌇 18:00", callback_data="remind_18:00")],
        [InlineKeyboardButton(text="🌆 19:00", callback_data="remind_19:00"),
         InlineKeyboardButton(text="🌙 21:00", callback_data="remind_21:00")],
        [InlineKeyboardButton(text="❌ Disable", callback_data="remind_off")],
    ])


def flashcard_kb(word_id: int, lang: str) -> InlineKeyboardMarkup:
    if lang == "ru":
        labels = [("😕 Не знал", 1), ("🤔 Почти", 2), ("😊 Помнил", 4), ("✅ Легко", 5)]
    else:
        labels = [("😕 Forgot", 1), ("🤔 Hard", 2), ("😊 Good", 4), ("✅ Easy", 5)]
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, callback_data=f"fc_{word_id}_{q}")
        for label, q in labels
    ]])


# ══════════════════════════════════════════════════════════════════
#  ПЛАНИРОВЩИК
# ══════════════════════════════════════════════════════════════════

async def send_reminder(uid: int):
    user = get_user(uid)
    if not user: return
    lang   = user.get("lang", "ru")
    streak = get_streak_count(uid)

    # Проверяем есть ли слова на повторение
    due = get_due_words(uid, limit=3)
    due_text = ""
    if due:
        count = len(due)
        due_text = f"\n📅 <b>{count} слов{'а' if count>1 else o} ждут повторения!</b> /vocab" if lang=="ru" else f"\n📅 <b>{count} word{'s' if count>1 else ''} due for review!</b> /vocab"

    msgs_ru = [
        "📚 <b>Время практики!</b>\nДаже 10 минут в день меняют всё.{due}",
        "🔥 <b>Не теряй прогресс!</b>\nALEX ждёт тебя.{due}",
        "⚡️ <b>Ежедневная практика = беглый English.</b>{due}",
    ]
    msgs_en = [
        "📚 <b>Practice time!</b>\nEven 10 minutes a day makes a difference.{due}",
        "🔥 <b>Keep your streak alive!</b>\nALEX is waiting.{due}",
        "⚡️ <b>Daily practice = fluent English.</b>{due}",
    ]
    msgs = msgs_ru if lang == "ru" else msgs_en
    text = random.choice(msgs).format(due=due_text)
    if streak > 2:
        text += f"\n\n🔥 <b>Streak: {streak} {'дней' if lang=='ru' else 'days'}!</b>"
    try:
        await bot.send_message(uid, text)
    except Exception as e:
        logger.warning(f"Reminder failed {uid}: {e}")


async def send_daily_quiz(uid: int):
    """Ежедневный квиз из личного словаря."""
    due = get_due_words(uid, limit=5)
    if not due: return
    lang = get_lang(uid)
    title = "📅 <b>Ежедневный квиз!</b>" if lang == "ru" else "📅 <b>Daily Word Quiz!</b>"
    text  = title + "\n\n"
    for i, w in enumerate(due, 1):
        text += f"{i}. <b>{w['word']}</b> — ?\n"
    hint = "Напиши перевод или используй в предложении 👇" if lang == "ru" else "Write the translation or use in a sentence 👇"
    text += f"\n<i>{hint}</i>"
    try:
        await bot.send_message(uid, text)
        # Сохраняем что квиз отправлен
        waiting[uid] = "daily_quiz"
        set_ctx(uid, quiz_words=[dict(w) for w in due])
    except Exception: pass


async def send_weekly_report(uid: int):
    stats = get_full_stats(uid)
    lang  = get_lang(uid)
    try:
        await bot.send_message(uid,
            f"📊 <b>{'Еженедельный отчёт' if lang=='ru' else 'Weekly Report'}</b>\n\n"
            f"🎯 {stats['level']} · {stats['rank']}\n"
            f"⭐ XP: <b>{stats['xp']}</b>\n"
            f"🔥 Streak: <b>{stats['streak']} {'дней' if lang=='ru' else 'days'}</b>\n"
            f"📅 Sessions: <b>{stats['sessions']}</b>\n"
            f"📝 Words: <b>{stats['words']}</b>\n"
            f"✅ Tests: <b>{stats['tests']}</b>"
        )
    except Exception: pass


def schedule_all():
    from database import db as dbq
    rows = dbq("SELECT uid, remind_time FROM users WHERE remind_time IS NOT NULL AND remind_time != 'off'", fetch=True)
    if rows:
        for r in rows:
            try:
                h, m = map(int, r["remind_time"].split(":"))
                scheduler.add_job(send_reminder, "cron", hour=h, minute=m, args=[r["uid"]], id=f"remind_{r['uid']}", replace_existing=True)
                # Квиз через час после основного напоминания
                scheduler.add_job(send_daily_quiz, "cron", hour=(h+1)%24, minute=m, args=[r["uid"]], id=f"quiz_{r['uid']}", replace_existing=True)
            except Exception: pass
    from database import db as dbq2
    all_users = dbq2("SELECT uid FROM users", fetch=True)
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
        await message.answer("🌍 Choose your interface language / Выбери язык:", reply_markup=lang_kb())
        return

    lang = get_lang(uid)
    welcome = (
        f"<b>{'Привет' if lang=='ru' else 'Hey'}, {name}!</b> 👋\n\n"
        f"Я <b>ALEX</b> — {'твой репетитор английского на базе ИИ' if lang=='ru' else 'your AI English tutor'}.\n\n"
        f"{'Работаю как живой преподаватель: объясняю, исправляю, веду ролевые диалоги, готовлю к TOEFL.' if lang=='ru' else 'I work like a real teacher: explain, correct, roleplay, and prep you for TOEFL.'}\n\n"
        f"<i>{'Сначала выбери уровень 👇' if lang=='ru' else 'First, set your level 👇'}</i>"
    )
    await message.answer(welcome, reply_markup=main_kb(lang))
    await message.answer(
        f"🎯 <b>{'Выбери уровень или пройди тест:' if lang=='ru' else 'Choose your level or take a placement test:'}</b>",
        reply_markup=level_kb()
    )


@dp.message(Command("lang"))
async def cmd_lang(message: Message):
    await message.answer("🌍 Choose language / Выбери язык:", reply_markup=lang_kb())


@dp.message(Command("level"))
async def cmd_level(message: Message):
    lang = get_lang(message.from_user.id)
    await message.answer(
        f"🎯 <b>{'Выбери уровень:' if lang=='ru' else 'Choose your level:'}</b>",
        reply_markup=level_kb()
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    if lang == "ru":
        text = (
            "<b>LinguaMax ALEX · Команды</b>\n\n"
            "<b>Обучение:</b>\n"
            "/lesson — уроки грамматики\n"
            "/vocab — словарный тренажёр\n"
            "/roleplay — ролевые диалоги\n"
            "/talk — разговорная практика\n"
            "/idioms — идиомы\n"
            "/writing — проверка текста\n"
            "/sentence — конструктор предложений\n\n"
            "<b>Тесты:</b>\n"
            "/test — тесты\n"
            "/toefl — подготовка к TOEFL\n\n"
            "<b>Прогресс:</b>\n"
            "/stats — статистика\n"
            "/mistakes — мои ошибки\n"
            "/level — сменить уровень\n"
            "/streak — мой стрик\n\n"
            "<b>Прочее:</b>\n"
            "/interests — задать интересы\n"
            "/remind — напоминания\n"
            "/reset — сбросить диалог\n\n"
            "📸 Фото текста → ALEX проанализирует\n"
            "✍️ Пиши по-английски → ALEX исправит"
        )
    else:
        text = (
            "<b>LinguaMax ALEX · Commands</b>\n\n"
            "<b>Learning:</b>\n"
            "/lesson — grammar lessons\n"
            "/vocab — vocabulary trainer\n"
            "/roleplay — roleplay scenarios\n"
            "/talk — speaking practice\n"
            "/idioms — idioms\n"
            "/writing — text correction\n"
            "/sentence — sentence builder\n\n"
            "<b>Tests:</b>\n"
            "/test — tests\n"
            "/toefl — TOEFL preparation\n\n"
            "<b>Progress:</b>\n"
            "/stats — statistics\n"
            "/mistakes — my errors\n"
            "/level — change level\n"
            "/streak — my streak\n\n"
            "<b>Other:</b>\n"
            "/interests — set your interests\n"
            "/remind — reminders\n"
            "/reset — clear dialogue\n\n"
            "📸 Photo of text → ALEX analyzes it\n"
            "✍️ Write in English → ALEX corrects it"
        )
    await message.answer(text)


@dp.message(Command("lesson"))
async def cmd_lesson(message: Message):
    lang = get_lang(message.from_user.id)
    title = "📚 <b>Уроки грамматики:</b>" if lang == "ru" else "📚 <b>Grammar Lessons:</b>"
    await message.answer(title, reply_markup=lesson_kb(lang))


@dp.message(Command("vocab"))
async def cmd_vocab(message: Message):
    lang = get_lang(message.from_user.id)
    title = "📝 <b>Словарный тренажёр:</b>" if lang == "ru" else "📝 <b>Vocabulary Trainer:</b>"
    await message.answer(title, reply_markup=vocab_kb(lang))


@dp.message(Command("roleplay"))
async def cmd_roleplay(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    title = "🎭 <b>Ролевые диалоги — выбери сценарий:</b>" if lang == "ru" else "🎭 <b>Roleplay — choose a scenario:</b>"
    await message.answer(title, reply_markup=roleplay_kb(lang))


@dp.message(Command("talk"))
async def cmd_talk(message: Message):
    lang = get_lang(message.from_user.id)
    title = "💬 <b>Разговорная практика:</b>" if lang == "ru" else "💬 <b>Speaking Practice:</b>"
    await message.answer(title, reply_markup=talk_kb(lang))


@dp.message(Command("test"))
async def cmd_test(message: Message):
    lang = get_lang(message.from_user.id)
    title = "✅ <b>Тесты:</b>" if lang == "ru" else "✅ <b>Tests:</b>"
    await message.answer(title, reply_markup=test_kb(lang))


@dp.message(Command("toefl"))
async def cmd_toefl(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    text = (
        "🎓 <b>Подготовка к TOEFL iBT</b>\n\n"
        "Тест состоит из 4 секций:\n"
        "📖 Reading · 🎧 Listening · 🗣 Speaking · ✍️ Writing\n\n"
        "<i>Выбери секцию для тренировки:</i>"
        if lang == "ru" else
        "🎓 <b>TOEFL iBT Preparation</b>\n\n"
        "The test has 4 sections:\n"
        "📖 Reading · 🎧 Listening · 🗣 Speaking · ✍️ Writing\n\n"
        "<i>Choose a section to practice:</i>"
    )
    await message.answer(text, reply_markup=toefl_kb(lang))


@dp.message(Command("writing"))
async def cmd_writing(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    prompt = (
        "✍️ <b>Проверка текста</b>\n\n"
        "Отправь свой текст на английском — ALEX вернёт:\n"
        "✅ Исправленная версия\n"
        "🌟 Улучшенная (native-like)\n"
        "📚 Разбор каждой ошибки"
        if lang == "ru" else
        "✍️ <b>Writing Check</b>\n\n"
        "Send your English text — ALEX will return:\n"
        "✅ Corrected version\n"
        "🌟 Native-like improvement\n"
        "📚 Breakdown of each error"
    )
    await message.answer(prompt)
    waiting[uid] = "writing"


@dp.message(Command("sentence"))
async def cmd_sentence(message: Message):
    uid   = message.from_user.id
    level = get_level(uid)
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_alex(uid,
        f"Give me a sentence builder exercise for {level} level. "
        "Provide 6-8 jumbled words and ask me to arrange them into a correct sentence. "
        "After I answer, confirm if correct or show the right answer with explanation.",
        mode="grammar"
    )
    await message.answer(reply)
    log_session(uid, "sentence_builder")
    waiting[uid] = "lesson_active"


@dp.message(Command("idioms"))
async def cmd_idioms(message: Message):
    uid   = message.from_user.id
    level = get_level(uid)
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await ask_alex(uid,
        f"Teach me 5 useful English idioms for {level} level. "
        "For each: the idiom, meaning, origin (very briefly), 2 natural example sentences, "
        "and when native speakers actually use it. Make it memorable.",
        mode="vocab"
    )
    await message.answer(reply)
    log_session(uid, "idioms")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    stats = get_full_stats(uid)
    xp    = stats["xp"]
    next_rank_xp = {
        "🌱 Seedling": 100, "📗 Beginner": 300, "📘 Elementary": 600,
        "📙 Pre-Intermediate": 1000, "⭐ Intermediate": 1500,
        "🌟 Upper-Intermediate": 2500, "💫 Advanced": 4000, "🏆 Master": 9999
    }
    nxt = next_rank_xp.get(stats["rank"], 9999)
    progress_bar = "█" * min(10, int(xp/nxt*10)) + "░" * max(0, 10-int(xp/nxt*10))

    text = (
        f"📊 <b>Твой прогресс</b>\n\n"
        f"🎯 Уровень: <b>{stats['level']}</b>\n"
        f"{stats['rank']}\n"
        f"⭐ XP: <b>{xp}</b> [{progress_bar}]\n\n"
        f"🔥 Стрик: <b>{stats['streak']} дней</b>\n"
        f"📅 Занятий: <b>{stats['sessions']}</b>\n"
        f"✅ Тестов: <b>{stats['tests']}</b>\n"
        f"📝 Слов в словаре: <b>{stats['words']}</b>\n"
        f"❌ Ошибок разобрано: <b>{stats['errors']}</b>\n"
        f"🎓 TOEFL сессий: <b>{stats['toefl']}</b>"
        if lang == "ru" else
        f"📊 <b>Your Progress</b>\n\n"
        f"🎯 Level: <b>{stats['level']}</b>\n"
        f"{stats['rank']}\n"
        f"⭐ XP: <b>{xp}</b> [{progress_bar}]\n\n"
        f"🔥 Streak: <b>{stats['streak']} days</b>\n"
        f"📅 Sessions: <b>{stats['sessions']}</b>\n"
        f"✅ Tests: <b>{stats['tests']}</b>\n"
        f"📝 Words learned: <b>{stats['words']}</b>\n"
        f"❌ Errors reviewed: <b>{stats['errors']}</b>\n"
        f"🎓 TOEFL sessions: <b>{stats['toefl']}</b>"
    )
    await message.answer(text)


@dp.message(Command("mistakes"))
async def cmd_mistakes(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    rows  = get_mistakes(uid, limit=10)
    if not rows:
        msg = "✅ Ошибок пока не записано. Напиши что-нибудь по-английски!" if lang=="ru" else "✅ No mistakes recorded yet. Write something in English!"
        await message.answer(msg)
        return
    title = "❌ <b>Твои последние ошибки:</b>\n\n" if lang=="ru" else "❌ <b>Your recent mistakes:</b>\n\n"
    text  = title
    for i, r in enumerate(rows, 1):
        text += f"{i}. ❌ <code>{r['original'][:60]}</code>\n   ✅ <i>{r['corrected'][:60]}</i>\n   💡 {r['explanation'][:100]}\n\n"
    await message.answer(text)


@dp.message(Command("streak"))
async def cmd_streak(message: Message):
    uid    = message.from_user.id
    lang   = get_lang(uid)
    streak = get_streak_count(uid)
    xp     = get_xp(uid)
    rank   = get_rank(xp)
    if streak > 0:
        msg = f"🔥 <b>Стрик: {streak} дней подряд!</b>\n{rank} · {xp} XP" if lang=="ru" else f"🔥 <b>Streak: {streak} days in a row!</b>\n{rank} · {xp} XP"
    else:
        msg = "🎯 Начни стрик сегодня — занимайся каждый день!" if lang=="ru" else "🎯 Start your streak today — study every day!"
    await message.answer(msg)


@dp.message(Command("interests"))
async def cmd_interests(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    msg  = (
        "🎮 <b>Расскажи о своих интересах!</b>\n\n"
        "Напиши через запятую (например: <code>gaming, music, travel, tech</code>)\n\n"
        "ALEX будет строить примеры и упражнения на основе того что тебя интересует."
        if lang == "ru" else
        "🎮 <b>Tell me about your interests!</b>\n\n"
        "Write comma-separated (e.g. <code>gaming, music, travel, tech</code>)\n\n"
        "ALEX will use examples and exercises based on your interests."
    )
    await message.answer(msg)
    waiting[uid] = "set_interests"


@dp.message(Command("remind"))
async def cmd_remind(message: Message):
    await message.answer("⏰ Set daily reminder:", reply_markup=remind_kb())


@dp.message(Command("reset"))
async def cmd_reset(message: Message):
    uid  = message.from_user.id
    lang = get_lang(uid)
    clear_history(uid)
    waiting.pop(uid, None)
    clear_ctx(uid)
    msg = "🔄 <b>Диалог сброшен.</b>" if lang == "ru" else "🔄 <b>Dialogue reset.</b>"
    await message.answer(msg)

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
    welcome = (
        f"<b>Привет, {name}!</b> 👋\n\n"
        "Я <b>ALEX</b> — твой репетитор английского.\n\n"
        "<i>Выбери уровень 👇</i>"
        if lang == "ru" else
        f"<b>Hey, {name}!</b> 👋\n\n"
        "I'm <b>ALEX</b> — your English tutor.\n\n"
        "<i>Set your level 👇</i>"
    )
    await cb.message.answer(welcome, reply_markup=main_kb(lang))
    await cb.message.answer(
        "🎯 <b>Choose your level or take a placement test:</b>",
        reply_markup=level_kb()
    )


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
        f"My English level is {level}. Give me a brief, encouraging welcome. Tell me what we'll focus on at this level and suggest the best way to start today. Be specific and motivating.",
        mode="general"
    )
    await cb.message.answer(reply)
    log_session(uid, "level_set")


@dp.callback_query(F.data.startswith("rp_"))
async def cb_roleplay(cb: CallbackQuery):
    uid      = cb.from_user.id
    lang     = get_lang(uid)
    scenario = ROLEPLAY_SCENARIOS.get(cb.data)
    if not scenario:
        await cb.answer()
        return

    if cb.data == "rp_custom":
        await cb.answer()
        await cb.message.answer(
            "🎭 Опиши свою ситуацию для ролевого диалога:" if lang == "ru" else
            "🎭 Describe your custom roleplay scenario:"
        )
        waiting[uid] = "rp_custom"
        return

    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    clear_history(uid)
    set_ctx(uid, mode="roleplay", scenario=cb.data)
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, scenario["prompt"], mode="roleplay")
    await cb.message.answer(reply)
    log_session(uid, f"roleplay_{cb.data}")
    waiting[uid] = "roleplay_active"


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
    uid    = cb.from_user.id
    lang   = get_lang(uid)

    if cb.data == "daily_quiz" or cb.data == "vocab_review":
        due = get_due_words(uid, limit=5)
        if not due:
            msg = "✅ Нет слов для повторения сегодня! Словарь в порядке." if lang=="ru" else "✅ No words due for review today! Your vocabulary is up to date."
            await cb.answer()
            await cb.message.answer(msg)
            return
        # Запускаем флэш-карточки
        await cb.message.edit_reply_markup(reply_markup=None)
        await cb.answer()
        word = dict(due[0])
        set_ctx(uid, review_queue=[dict(w) for w in due], review_idx=0)
        msg = (
            f"🃏 <b>Флэш-карточка 1/{len(due)}</b>\n\n"
            f"📖 <b>{word['word']}</b>\n\n"
            f"<i>{word['example']}</i>\n\n"
            f"Помнишь перевод?"
            if lang == "ru" else
            f"🃏 <b>Flashcard 1/{len(due)}</b>\n\n"
            f"📖 <b>{word['word']}</b>\n\n"
            f"<i>{word['example']}</i>\n\n"
            f"Do you remember the translation?"
        )
        await cb.message.answer(msg, reply_markup=flashcard_kb(word["id"], lang))
        return

    prompt = VOCAB_PROMPTS.get(cb.data,"")
    if not prompt:
        await cb.answer()
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode="vocab")
    await cb.message.answer(reply)
    log_session(uid, cb.data)
    waiting[uid] = "vocab_active"


@dp.callback_query(F.data.startswith("fc_"))
async def cb_flashcard(cb: CallbackQuery):
    """Обработка оценки флэш-карточки."""
    uid    = cb.from_user.id
    lang   = get_lang(uid)
    parts  = cb.data.split("_")
    word_id = int(parts[1])
    quality = int(parts[2])

    update_word_review(word_id, quality)
    add_xp(uid, 3)

    ctx = get_ctx(uid)
    queue = ctx.get("review_queue", [])
    idx   = ctx.get("review_idx", 0) + 1
    set_ctx(uid, review_idx=idx)

    await cb.message.edit_reply_markup(reply_markup=None)

    # Показываем ответ
    from database import db as dbq
    word_data = dbq("SELECT * FROM vocabulary WHERE id=?", (word_id,), fetch=True)
    if word_data:
        w = dict(word_data[0])
        await cb.message.answer(
            f"✅ <b>{w['word']}</b> = {w['translation']}\n<i>{w['example']}</i>"
        )

    if idx < len(queue):
        word = queue[idx]
        msg = (
            f"🃏 <b>Карточка {idx+1}/{len(queue)}</b>\n\n"
            f"📖 <b>{word['word']}</b>\n\n<i>{word['example']}</i>\n\nПомнишь?"
            if lang == "ru" else
            f"🃏 <b>Card {idx+1}/{len(queue)}</b>\n\n"
            f"📖 <b>{word['word']}</b>\n\n<i>{word['example']}</i>\n\nDo you remember?"
        )
        await cb.message.answer(msg, reply_markup=flashcard_kb(word["id"], lang))
    else:
        done = "✅ <b>Повторение завершено!</b> +{xp} XP 🎉" if lang=="ru" else "✅ <b>Review complete!</b> +{xp} XP 🎉"
        await cb.message.answer(done.format(xp=len(queue)*3))
        log_session(uid, "vocab_review", score=len(queue), total=len(queue))
        clear_ctx(uid)

    await cb.answer()


@dp.callback_query(F.data.startswith("test_") | F.data.startswith("toefl_") | F.data.startswith("talk_"))
async def cb_section(cb: CallbackQuery):
    uid  = cb.from_user.id
    lang = get_lang(uid)

    if cb.data == "toefl_score":
        await cb.answer()
        rows = get_toefl_scores(uid)
        if not rows:
            msg = "🎓 Баллов TOEFL пока нет. Начни практику!" if lang=="ru" else "🎓 No TOEFL scores yet. Start practicing!"
            await cb.message.answer(msg)
            return
        text = "🎓 <b>TOEFL Progress:</b>\n\n"
        for r in rows:
            text += f"📌 <b>{r['section']}</b>: best {r['best']}, avg {r['avg_s']:.0f} ({r['cnt']} sessions)\n"
        await cb.message.answer(text)
        return

    # Определяем промпт и режим
    all_prompts = {**TEST_PROMPTS, **TOEFL_PROMPTS, **TALK_PROMPTS}
    mode_map = {}
    for k in TEST_PROMPTS:   mode_map[k] = "test"
    for k in TOEFL_PROMPTS:  mode_map[k] = "toefl"
    for k in TALK_PROMPTS:   mode_map[k] = "speaking"

    prompt = all_prompts.get(cb.data,"")
    mode   = mode_map.get(cb.data, "general")

    if not prompt:
        await cb.answer()
        return

    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer()
    await bot.send_chat_action(cb.message.chat.id, "typing")
    reply = await ask_alex(uid, prompt, mode=mode)
    await cb.message.answer(reply)
    log_session(uid, cb.data)

    state_map = {
        "test": "test_active", "toefl": "toefl_active", "speaking": "speaking_active"
    }
    waiting[uid] = state_map.get(mode, "active")


@dp.callback_query(F.data.startswith("remind_"))
async def cb_remind(cb: CallbackQuery):
    uid  = cb.from_user.id
    data = cb.data.replace("remind_","")
    if data == "off":
        update_user(uid, remind_time="off")
        for jid in [f"remind_{uid}", f"quiz_{uid}"]:
            if scheduler.get_job(jid): scheduler.remove_job(jid)
        await cb.answer()
        await cb.message.edit_text("❌ Reminders disabled.")
    else:
        update_user(uid, remind_time=data)
        try:
            h, m = map(int, data.split(":"))
            scheduler.add_job(send_reminder,"cron",hour=h,minute=m,args=[uid],id=f"remind_{uid}",replace_existing=True)
            scheduler.add_job(send_daily_quiz,"cron",hour=(h+1)%24,minute=m,args=[uid],id=f"quiz_{uid}",replace_existing=True)
        except Exception as e: logger.warning(e)
        await cb.answer(f"✅ {data}")
        await cb.message.edit_text(f"✅ <b>Reminder set: {data}</b>\n\nI'll send you a daily quiz too! 📅")


# ══════════════════════════════════════════════════════════════════
#  ФОТО
# ══════════════════════════════════════════════════════════════════

@dp.message(F.photo)
async def handle_photo(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    photo: PhotoSize = message.photo[-1]
    fi    = await bot.get_file(photo.file_id)
    fb    = await bot.download_file(fi.file_path)
    pb    = fb.read() if hasattr(fb,"read") else bytes(fb)
    msg   = "📸 Анализирую текст..." if lang=="ru" else "📸 Analyzing text..."
    await message.answer(msg)
    await bot.send_chat_action(message.chat.id, "typing")
    reply = await analyze_photo(uid, pb)
    await message.answer(reply)
    log_session(uid, "photo_analysis")

# ══════════════════════════════════════════════════════════════════
#  СВОБОДНЫЙ ТЕКСТ
# ══════════════════════════════════════════════════════════════════

MENU_RU = {
    "📚 Урок грамматики":  "lesson",
    "📝 Словарь":          "vocab",
    "🎭 Ролевой диалог":   "roleplay",
    "✅ Тест":             "test",
    "🎓 TOEFL":            "toefl",
    "✍️ Проверить текст":  "writing",
    "💬 Разговор":         "talk",
    "🗣 Идиомы":           "idioms",
    "❌ Мои ошибки":       "mistakes",
    "📊 Прогресс":         "stats",
}
MENU_EN = {
    "📚 Grammar Lesson":   "lesson",
    "📝 Vocabulary":       "vocab",
    "🎭 Roleplay":         "roleplay",
    "✅ Test":             "test",
    "🎓 TOEFL":            "toefl",
    "✍️ Check Writing":    "writing",
    "💬 Speaking":         "talk",
    "🗣 Idioms":           "idioms",
    "❌ My Mistakes":      "mistakes",
    "📊 Progress":         "stats",
}


@dp.message(F.text)
async def handle_text(message: Message):
    uid   = message.from_user.id
    lang  = get_lang(uid)
    text  = message.text.strip()
    state = waiting.get(uid, "")

    # ── Кнопки меню ───────────────────────────────────────────────
    menu = MENU_RU if lang == "ru" else MENU_EN
    action = menu.get(text)
    if action:
        if action == "lesson":   await cmd_lesson(message)
        elif action == "vocab":  await cmd_vocab(message)
        elif action == "roleplay": await cmd_roleplay(message)
        elif action == "test":   await cmd_test(message)
        elif action == "toefl":  await cmd_toefl(message)
        elif action == "writing": await cmd_writing(message)
        elif action == "talk":   await cmd_talk(message)
        elif action == "idioms": await cmd_idioms(message)
        elif action == "mistakes": await cmd_mistakes(message)
        elif action == "stats":  await cmd_stats(message)
        return

    # ── Состояния ─────────────────────────────────────────────────

    if state == "set_interests":
        waiting.pop(uid, None)
        update_user(uid, interests=text[:200])
        msg = f"✅ Запомнил: <b>{text}</b>\n\nТеперь буду строить примеры на основе твоих интересов!" if lang=="ru" else f"✅ Got it: <b>{text}</b>\n\nI'll use your interests in all examples now!"
        await message.answer(msg)
        return

    if state == "rp_custom":
        waiting.pop(uid, None)
        clear_history(uid)
        set_ctx(uid, mode="roleplay")
        await bot.send_chat_action(message.chat.id, "typing")
        prompt = f"Start a roleplay scenario based on this situation: {text}. Begin immediately in character."
        reply  = await ask_alex(uid, prompt, mode="roleplay")
        await message.answer(reply)
        log_session(uid, "roleplay_custom")
        waiting[uid] = "roleplay_active"
        return

    if state == "writing":
        waiting.pop(uid, None)
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid,
            f"Please analyze and correct this text using the 3-layer system:\n\n{text}",
            mode="correction"
        )
        await message.answer(reply)
        log_session(uid, "writing_check")
        if len(text) > 20:
            log_mistake(uid, text[:100], "See correction", "writing exercise", "mixed")
        return

    if state == "daily_quiz":
        # Ответ на ежедневный квиз
        ctx   = get_ctx(uid)
        words = ctx.get("quiz_words", [])
        waiting.pop(uid, None)
        await bot.send_chat_action(message.chat.id, "typing")
        words_str = ", ".join(w["word"] for w in words)
        reply = await ask_alex(uid,
            f"The student answered the daily vocabulary quiz. The words were: {words_str}. Their answer: {text}. Check their answers and give feedback.",
            mode="vocab"
        )
        await message.answer(reply)
        log_session(uid, "daily_quiz")
        clear_ctx(uid)
        return

    # ── Активные сессии ───────────────────────────────────────────
    if state in ("test_active","lesson_active","toefl_active","vocab_active","roleplay_active","speaking_active"):
        mode_map = {
            "test_active": "test", "lesson_active": "grammar",
            "toefl_active": "toefl", "vocab_active": "vocab",
            "roleplay_active": "roleplay", "speaking_active": "speaking",
        }
        mode = mode_map.get(state, "general")

        # Кнопка "Объясни иначе"
        lower = text.lower()
        if any(p in lower for p in ["объясни иначе", "не понял", "explain differently", "i don't get it", "другой пример"]):
            await bot.send_chat_action(message.chat.id, "typing")
            reply = await ask_alex(uid,
                "Please explain the same concept using a completely different analogy or approach. "
                "Try a metaphor, a story, or a real-world comparison. Make it even simpler.",
                mode=mode
            )
            await message.answer(reply)
            return

        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode=mode)
        await message.answer(reply)
        return

    # ── Автоопределение: английский текст → коррекция ─────────────
    en_ratio = sum(1 for c in text if c.isalpha() and ord(c) < 128) / max(len(text),1)

    if en_ratio > 0.6 and len(text) > 8:
        await bot.send_chat_action(message.chat.id, "typing")
        reply = await ask_alex(uid, text, mode="correction",
            extra="The student wrote in English. If there are errors: correct them clearly. "
                  "If it's perfect: compliment and expand the conversation. Keep it natural."
        )
        await message.answer(reply)
        log_session(uid, "free_writing")
        if len(text) > 15:
            log_mistake(uid, text[:100], "See correction", "free writing", "mixed")
    else:
        # Вопрос на родном языке
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
    logger.info("🎓 LinguaMax ALEX Ultimate — запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
